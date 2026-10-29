"""Small OpenAI-compatible chat-completions client with ordered provider fallback.

API keys are read from environment variables only. Provider error bodies and
authorization headers are deliberately never included in returned errors.
"""

import json
import os
import socket
import time
from typing import Mapping, NamedTuple, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 128 * 1024
MAX_MESSAGE_CHARS = 24000
MAX_MESSAGES = 8
PROVIDER_TIMEOUT_SECONDS = 10


class ProviderSpec(NamedTuple):
    name: str
    key_env: str
    model_env: str
    endpoint_env: str
    endpoint: str
    default_model: Optional[str]


PROVIDERS = (
    ProviderSpec(
        "OpenRouter",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1/chat/completions",
        None,
    ),
    ProviderSpec(
        "OpenAI",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "https://api.openai.com/v1/chat/completions",
        "gpt-6-astra",
    ),
    ProviderSpec(
        "Grok",
        "GROK_API_KEY",
        "GROK_MODEL",
        "GROK_BASE_URL",
        "https://api.x.ai/v1/chat/completions",
        "grok-4.7",
    ),
    ProviderSpec(
        "DeepSeek",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_BASE_URL",
        "https://api.deepseek.com/chat/completions",
        "deepseek-flash",
    ),
)


class ProviderHTTPError(Exception):
    """HTTP status only; deliberately excludes the provider response body."""

    def __init__(self, status):
        self.status = int(status)
        Exception.__init__(self, "HTTP %d" % self.status)


class ResponseTooLarge(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class ProviderUnavailable(Exception):
    """A safe, structured summary of all provider attempts."""

    def __init__(self, attempts):
        self.attempts = list(attempts)
        Exception.__init__(self, "No configured AI provider completed the request.")

    def as_dict(self):
        return {
            "category": "providers_unavailable",
            "message": (
                "AI coordination is unavailable. You can still run the existing "
                "Python analysis tools independently."
            ),
            "attempts": list(self.attempts),
        }


def _validate_messages(messages):
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise ValueError("messages must contain between 1 and %d items" % MAX_MESSAGES)
    checked = []
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise ValueError("each message must contain only role and content")
        if message["role"] not in ("system", "user", "assistant"):
            raise ValueError("unsupported message role")
        content = message["content"]
        if not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS:
            raise ValueError("message content is invalid or too long")
        checked.append({"role": message["role"], "content": content})
    return checked


def _validate_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 500:
        raise ValueError("endpoint is invalid")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ValueError("endpoint is invalid")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
        or not parsed.path.endswith("/chat/completions")
    ):
        raise ValueError("endpoint must be an HTTPS chat-completions URL")
    return endpoint


def _post_json(endpoint, headers, payload, timeout):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        opener = build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ProviderHTTPError(exc.code)
    except (socket.timeout, TimeoutError):
        raise
    except URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError()
        raise ConnectionError()
    except OSError:
        raise ConnectionError()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge()
    return raw


def _http_category(status):
    if status in (401, 403):
        return "authentication"
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "server_error"
    if status in (400, 404, 422):
        return "invalid_configuration_or_request"
    return "http_error"


def _response_text(raw):
    try:
        document = json.loads(raw.decode("utf-8"))
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError()
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError()
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
        content = content.strip()
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError()
        return content
    except (UnicodeDecodeError, ValueError, AttributeError, TypeError):
        raise ValueError("invalid provider response")


def chat_completion(
    messages, environ=None, post_json=None, deadline=None, validate_content=None
):
    """Return the first valid completion, falling back in the fixed provider order."""
    env = environ if environ is not None else __import__("os").environ
    post = post_json if post_json is not None else _post_json
    checked_messages = _validate_messages(messages)
    attempts = []

    for spec in PROVIDERS:
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            attempts.append({"provider": spec.name, "category": "deadline_exceeded"})
            break

        key = env.get(spec.key_env, "")
        if not isinstance(key, str) or not key.strip():
            attempts.append({"provider": spec.name, "category": "missing_key"})
            continue

        model = env.get(spec.model_env, "") or spec.default_model
        if spec.name != "OpenRouter" and (not isinstance(model, str) or not model.strip()):
            attempts.append({"provider": spec.name, "category": "missing_model"})
            continue
        if model is not None and (not isinstance(model, str) or len(model) > 200):
            attempts.append({"provider": spec.name, "category": "invalid_configuration"})
            continue

        endpoint = env.get(spec.endpoint_env, "") or spec.endpoint
        try:
            endpoint = _validate_endpoint(endpoint)
        except ValueError:
            attempts.append({"provider": spec.name, "category": "invalid_configuration"})
            continue

        payload = {"messages": checked_messages, "max_tokens": 1200, "stream": False}
        if model:
            payload["model"] = model.strip()
        timeout = PROVIDER_TIMEOUT_SECONDS
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                attempts.append({"provider": spec.name, "category": "deadline_exceeded"})
                break
            timeout = min(timeout, max(0.1, remaining))
        headers = {
            "Authorization": "Bearer " + key.strip(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            raw = post(endpoint, headers, payload, timeout)
            content = _response_text(raw)
        except ProviderHTTPError as exc:
            attempts.append(
                {"provider": spec.name, "category": _http_category(exc.status)}
            )
            continue
        except TimeoutError:
            attempts.append({"provider": spec.name, "category": "timeout"})
            continue
        except ConnectionError:
            attempts.append({"provider": spec.name, "category": "connection_error"})
            continue
        except ResponseTooLarge:
            attempts.append({"provider": spec.name, "category": "response_too_large"})
            continue
        except ValueError:
            attempts.append({"provider": spec.name, "category": "invalid_response"})
            continue
        except Exception:
            # Do not leak exception text: transport libraries can include URLs
            # and response details that should not be shown to users.
            attempts.append({"provider": spec.name, "category": "provider_error"})
            continue

        validated = None
        if validate_content is not None:
            try:
                validated = validate_content(content)
            except Exception:
                attempts.append({"provider": spec.name, "category": "invalid_response"})
                continue

        result = {
            "provider": spec.name,
            "model": model or "account default",
            "content": content,
            "attempts": attempts,
        }
        if validate_content is not None:
            result["validated"] = validated
        return result

    raise ProviderUnavailable(attempts)
