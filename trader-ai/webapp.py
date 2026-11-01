"""GE Nexus web API and PWA shell.

The browser can request only the named analysis endpoints below. It never
chooses scripts, arguments, providers, or filesystem paths.
"""

import json
import hmac
import math
import os
import re
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException


AI_DIR = Path(__file__).resolve().parent
ROOT = AI_DIR.parent
WEB_DIR = AI_DIR / "web"
sys.path.insert(0, str(AI_DIR))

import coordinator  # noqa: E402


MAX_BODY_BYTES = 16 * 1024
MAX_SYMBOLS = 12
MAX_REQUESTS_PER_WINDOW = 5
ANALYSIS_WINDOW_SECONDS = 15 * 60
MAX_SCANS_PER_WINDOW = 12
SCAN_WINDOW_SECONDS = 60
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$")
ACCESS_PASSWORD_ENV = "GE_NEXUS_ACCESS_PASSWORD"


def _validate_deployment_configuration(environ):
    if environ.get("RENDER", "").lower() == "true" and not environ.get(
        ACCESS_PASSWORD_ENV, ""
    ).strip():
        raise RuntimeError(
            "Set GE_NEXUS_ACCESS_PASSWORD in Render before starting GE Nexus."
        )


_validate_deployment_configuration(os.environ)

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/assets")
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES


class SlidingWindowLimiter:
    """Small per-process best-effort request limiter."""

    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, maximum, window):
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= window:
                events.popleft()
            if len(events) >= maximum:
                return False, max(1, math.ceil(window - (now - events[0])))
            events.append(now)
            return True, 0


_limiter = SlidingWindowLimiter()


@app.before_request
def require_deployment_password():
    password = os.environ.get(ACCESS_PASSWORD_ENV, "").strip()
    if not password or request.path == "/api/health":
        return None

    authorization = request.authorization
    if (
        authorization is not None
        and authorization.type.lower() == "basic"
        and hmac.compare_digest(authorization.username or "", "ge-nexus")
        and hmac.compare_digest(authorization.password or "", password)
    ):
        return None

    response = app.response_class(
        "GE Nexus requires HTTP Basic authentication.",
        status=401,
        mimetype="text/plain",
    )
    response.headers["WWW-Authenticate"] = 'Basic realm="GE Nexus", charset="UTF-8"'
    return response


def _api_error(category, message, status):
    return jsonify({"error": {"category": category, "message": message}}), status


def _remote_key():
    # Deliberately do not trust caller-supplied X-Forwarded-For headers.
    return request.remote_addr or "unknown"


def _check_origin():
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc.lower() == request.host.lower()
        and parsed.username is None
        and parsed.password is None
    )


def _json_body():
    if not request.is_json:
        return None, _api_error(
            "invalid_content_type", "Send a JSON request body.", 415
        )
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, _api_error("invalid_json", "The JSON body must be an object.", 400)
    return body, None


def _watchlist_summary():
    path = ROOT / "trader" / "watchlist.json"
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_size > coordinator.RUNTIME_FILE_LIMIT:
            return {"available": False, "reason": "no_valid_watchlist"}
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        return {"available": False, "reason": "no_saved_watchlist"}
    except (OSError, UnicodeError, ValueError):
        return {"available": False, "reason": "watchlist_unreadable"}

    if not isinstance(document, dict):
        return {"available": False, "reason": "watchlist_unreadable"}
    hits = document.get("hits")
    if not isinstance(hits, list):
        hits = []
    candidates = []
    for hit in hits[:12]:
        if not isinstance(hit, dict):
            continue
        symbol = hit.get("symbol")
        if not isinstance(symbol, str) or not TICKER_RE.fullmatch(symbol):
            continue
        setups = hit.get("setups", [])
        if not isinstance(setups, list):
            setups = []
        candidates.append(
            {
                "symbol": symbol,
                "setups": [
                    value[:60]
                    for value in setups[:8]
                    if isinstance(value, str) and value.strip()
                ],
            }
        )

    return {
        "available": True,
        "source": str(document.get("source", "unknown"))[:40],
        "created_at": str(document.get("created_at", ""))[:80],
        "as_of": str(document.get("as_of", ""))[:80],
        "candidates": candidates,
    }


def _provider_status():
    providers = []
    for priority, spec in enumerate(coordinator.provider.PROVIDERS, start=1):
        has_key = bool(os.environ.get(spec.key_env, "").strip())
        has_model = bool(os.environ.get(spec.model_env, "").strip())
        endpoint = os.environ.get(spec.endpoint_env) or spec.endpoint
        try:
            coordinator.provider._validate_endpoint(endpoint)
            endpoint_valid = True
        except ValueError:
            endpoint_valid = False
        configured = has_key and has_model and endpoint_valid
        providers.append(
            {
                "name": spec.name,
                "priority": priority,
                "configured": configured,
                "connection_verified": False,
                "state": (
                    "configured_unverified"
                    if configured
                    else "invalid_endpoint"
                    if has_key and has_model and not endpoint_valid
                    else "needs_configuration"
                ),
            }
        )
    return providers


def _execute_validated_action(tool, arguments, request_text):
    plan_document = {
        "version": 1,
        "intent": "web workflow",
        "actions": [
            {
                "id": "a1",
                "tool": tool,
                "arguments": arguments,
                "depends_on": [],
                "expected_output": coordinator.TOOL_CATALOG[tool]["output"],
            }
        ],
        "clarification": None,
    }
    plan = coordinator.validate_plan(plan_document, request_text)
    results, warnings = coordinator._run_actions(
        plan, time.monotonic() + coordinator.MAX_TOTAL_SECONDS
    )
    return results[0], warnings


@app.before_request
def protect_api_origin():
    if request.path.startswith("/api/") and not _check_origin():
        return _api_error("origin_rejected", "Cross-origin API requests are not allowed.", 403)
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; manifest-src 'self'; "
        "worker-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(HTTPException)
def handle_http_error(error):
    if request.path.startswith("/api/"):
        messages = {
            404: ("not_found", "That API route was not found."),
            413: ("request_too_large", "Keep the request body under 16 KB."),
            405: ("method_not_allowed", "That method is not supported for this route."),
        }
        category, message = messages.get(
            error.code, ("http_error", "The request could not be completed.")
        )
        return _api_error(category, message, error.code)
    return error


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return handle_http_error(error)
    app.logger.error("Unhandled web request failure")
    if request.path.startswith("/api/"):
        return _api_error(
            "internal_error", "The request failed. Check the server status and try again.", 500
        )
    return "GE Nexus could not load this page.", 500


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    response = send_from_directory(
        WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json"
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(
        WEB_DIR, "sw.js", mimetype="application/javascript"
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "ge-nexus"})


@app.get("/api/status")
def status():
    engine_available = all(
        (ROOT / path).is_file()
        for path in set(coordinator.SCRIPT_PATHS.values())
    )
    return jsonify(
        {
            "service": "GE Nexus",
            "engine": {
                "available": engine_available,
                "interface": "existing Python tools",
            },
            "providers": _provider_status(),
            "provider_checks_are_live": False,
            "watchlist": _watchlist_summary(),
            "analysis_history": {"available": False, "persistent": False},
            "capabilities": {
                "natural_language_coordination": True,
                "market_scanner": True,
                "broker_execution": False,
            },
        }
    )


@app.get("/api/clock")
def exchange_clock():
    result, _warnings = _execute_validated_action(
        "alerts_clock", {}, "Check the exchange clock"
    )
    status_code = 200 if result.get("status") == "completed" else 502
    return jsonify(result), status_code


@app.post("/api/analyze")
def analyze():
    body, error = _json_body()
    if error:
        return error
    text = body.get("request")
    if not isinstance(text, str) or not text.strip():
        return _api_error("invalid_request", "Enter an analysis request.", 400)
    if len(text) > coordinator.MAX_REQUEST_CHARS:
        return _api_error(
            "request_too_long",
            "Keep the analysis request to %d characters or fewer."
            % coordinator.MAX_REQUEST_CHARS,
            400,
        )
    allowed, retry_after = _limiter.allow(
        ("analysis", _remote_key()),
        MAX_REQUESTS_PER_WINDOW,
        ANALYSIS_WINDOW_SECONDS,
    )
    if not allowed:
        response = jsonify(
            {
                "error": {
                    "category": "rate_limited",
                    "message": "Too many analysis requests. Wait before trying again.",
                }
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    result = coordinator.coordinate(text)
    status_code = 503 if result.get("status") == "unavailable" else 200
    return jsonify(result), status_code


@app.post("/api/scanner")
def scan():
    body, error = _json_body()
    if error:
        return error
    mode = body.get("mode")
    symbols = body.get("symbols")
    if mode not in ("daily", "orb"):
        return _api_error("invalid_mode", "Choose daily or opening-range scan.", 400)
    if (
        not isinstance(symbols, list)
        or not 1 <= len(symbols) <= MAX_SYMBOLS
        or any(not isinstance(symbol, str) for symbol in symbols)
    ):
        return _api_error(
            "invalid_symbols",
            "Provide 1 to %d ticker symbols." % MAX_SYMBOLS,
            400,
        )
    normalized = [symbol.strip().upper() for symbol in symbols]
    if any(not TICKER_RE.fullmatch(symbol) for symbol in normalized):
        return _api_error("invalid_symbols", "One or more ticker symbols are invalid.", 400)
    if len(set(normalized)) != len(normalized):
        return _api_error("duplicate_symbols", "Remove duplicate ticker symbols.", 400)

    allowed, retry_after = _limiter.allow(
        ("scanner", _remote_key()), MAX_SCANS_PER_WINDOW, SCAN_WINDOW_SECONDS
    )
    if not allowed:
        response = jsonify(
            {
                "error": {
                    "category": "rate_limited",
                    "message": "Too many scans. Wait before trying again.",
                }
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    request_text = "Scan " + " ".join(normalized)
    tool = "scanner_daily" if mode == "daily" else "scanner_orb"
    result, warnings = _execute_validated_action(
        tool, {"symbols": normalized}, request_text
    )
    if warnings:
        result["runtime_input_warnings"] = warnings
    status_code = 200 if result.get("status") == "completed" else 502
    return jsonify(result), status_code


if __name__ == "__main__":
    # Development only. Deployment should use the documented Gunicorn command.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
