import json
import unittest

from _load import ROOT
import sys
import os

sys.path.insert(0, os.path.join(ROOT, "trader-ai"))
import provider


def completion(text="ok"):
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": text}}]}
    ).encode("utf-8")


def configured():
    return {
        "OPENROUTER_API_KEY": "router-test-secret",
        "OPENROUTER_MODEL": "openai/gpt-4",
        "OPENAI_API_KEY": "openai-test-secret",
        "GROK_API_KEY": "grok-test-secret",
        "DEEPSEEK_API_KEY": "deepseek-test-secret",
    }


class ProviderTests(unittest.TestCase):
    def test_first_provider_success_stops_fallback(self):
        calls = []

        def post(url, headers, payload, timeout):
            calls.append((url, headers, payload, timeout))
            return completion("router response")

        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=configured(),
            post_json=post,
        )
        self.assertEqual(result["provider"], "OpenRouter")
        self.assertEqual(result["content"], "router response")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][0], "https://openrouter.ai/api/v1/chat/completions"
        )
        self.assertNotIn("router-test-secret", json.dumps(calls[0][2]))

    def test_openrouter_rate_limit_falls_back_to_openai(self):
        calls = []

        def post(url, headers, payload, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise provider.ProviderHTTPError(429)
            return completion("openai response")

        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=configured(),
            post_json=post,
        )
        self.assertEqual(result["provider"], "OpenAI")
        self.assertEqual(
            calls,
            [
                "https://openrouter.ai/api/v1/chat/completions",
                "https://api.openai.com/v1/chat/completions",
            ],
        )
        self.assertEqual(result["attempts"], [
            {"provider": "OpenRouter", "category": "rate_limited"}
        ])

    def test_fallback_continues_in_order_through_grok_and_deepseek(self):
        calls = []

        def post(url, headers, payload, timeout):
            calls.append(url)
            if len(calls) < 4:
                raise provider.ProviderHTTPError(503)
            return completion("deepseek response")

        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=configured(),
            post_json=post,
        )
        self.assertEqual(result["provider"], "DeepSeek")
        self.assertEqual(
            calls,
            [
                "https://openrouter.ai/api/v1/chat/completions",
                "https://api.openai.com/v1/chat/completions",
                "https://api.x.ai/v1/chat/completions",
                "https://api.deepseek.com/chat/completions",
            ],
        )
        self.assertEqual(
            [item["provider"] for item in result["attempts"]],
            ["OpenRouter", "OpenAI", "Grok"],
        )

    def test_missing_keys_are_skipped_without_network_calls(self):
        calls = []
        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ={"DEEPSEEK_API_KEY": "deepseek-test-secret"},
            post_json=lambda *args: calls.append(args) or completion("ok"),
        )
        self.assertEqual(result["provider"], "DeepSeek")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [item["category"] for item in result["attempts"]],
            ["missing_key", "missing_key", "missing_key"],
        )

    def test_openrouter_without_model_is_skipped(self):
        env = configured()
        env.pop("OPENROUTER_MODEL")
        calls = []
        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=env,
            post_json=lambda *args: calls.append(args) or completion("openai response"),
        )
        self.assertEqual(result["provider"], "OpenAI")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["attempts"][0]["category"], "missing_model")

    def test_all_provider_failures_return_safe_structured_error(self):
        secret = "do-not-display-this-key"

        def fail(*args):
            raise provider.ProviderHTTPError(401)

        with self.assertRaises(provider.ProviderUnavailable) as context:
            provider.chat_completion(
                [{"role": "user", "content": "Analyze TSLA"}],
                environ={key: secret for key in (
                    "OPENROUTER_API_KEY", "OPENAI_API_KEY",
                    "GROK_API_KEY", "DEEPSEEK_API_KEY",
                )} | {"OPENROUTER_MODEL": "openai/gpt-4"},
                post_json=fail,
            )
        error = context.exception.as_dict()
        self.assertEqual(error["category"], "providers_unavailable")
        self.assertEqual(
            [item["category"] for item in error["attempts"]],
            ["authentication"] * 4,
        )
        self.assertNotIn(secret, json.dumps(error))

    def test_timeout_and_server_failures_advance_to_next_provider(self):
        calls = []

        def fail_then_succeed(url, headers, payload, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError()
            if len(calls) == 2:
                raise provider.ProviderHTTPError(502)
            return completion("grok response")

        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=configured(),
            post_json=fail_then_succeed,
        )
        self.assertEqual(result["provider"], "Grok")
        self.assertEqual(result["attempts"], [
            {"provider": "OpenRouter", "category": "timeout"},
            {"provider": "OpenAI", "category": "server_error"},
        ])
        self.assertTrue(all(0 < value <= provider.PROVIDER_TIMEOUT_SECONDS for value in calls))

    def test_authentication_status_is_categorized(self):
        def auth_then_succeed(url, headers, payload, timeout):
            if url.startswith("https://openrouter.ai"):
                raise provider.ProviderHTTPError(403)
            return completion("openai response")

        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=configured(),
            post_json=auth_then_succeed,
        )
        self.assertEqual(result["provider"], "OpenAI")
        self.assertEqual(result["attempts"][0]["category"], "authentication")

    def test_malformed_response_is_rejected_and_falls_back(self):
        calls = []

        def malformed_then_succeed(url, headers, payload, timeout):
            calls.append(url)
            if len(calls) == 1:
                return b'{"choices":[]}'
            return completion("valid response")

        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=configured(),
            post_json=malformed_then_succeed,
        )
        self.assertEqual(result["provider"], "OpenAI")
        self.assertEqual(result["attempts"][0]["category"], "invalid_response")

    def test_bad_endpoint_is_skipped_without_sending_a_key(self):
        calls = []
        env = configured()
        env["OPENROUTER_BASE_URL"] = "http://untrusted.invalid/chat/completions"
        result = provider.chat_completion(
            [{"role": "user", "content": "Analyze TSLA"}],
            environ=env,
            post_json=lambda *args: calls.append(args) or completion("openai response"),
        )
        self.assertEqual(result["provider"], "OpenAI")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["attempts"][0]["category"], "invalid_configuration")

    def test_provider_response_content_must_be_nonempty_text(self):
        for body in (
            b"not json",
            json.dumps({"choices": [{"message": {"content": ""}}]}).encode(),
            json.dumps({"choices": [{"message": {"content": ["not", "text"]}}]}).encode(),
        ):
            with self.subTest(body=body):
                with self.assertRaises(provider.ProviderUnavailable):
                    provider.chat_completion(
                        [{"role": "user", "content": "Analyze TSLA"}],
                        environ={"OPENROUTER_API_KEY": "router-test-secret"},
                        post_json=lambda *args, value=body: value,
                    )

    def test_invalid_content_schema_falls_back_to_next_provider(self):
        calls = []

        def malformed_then_valid(url, headers, payload, timeout):
            calls.append(url)
            if len(calls) == 1:
                return completion("not JSON")
            return completion('{"valid":true}')

        result = provider.chat_completion(
            [{"role": "user", "content": "Return JSON"}],
            environ=configured(),
            post_json=malformed_then_valid,
            validate_content=json.loads,
        )
        self.assertEqual(result["provider"], "OpenAI")
        self.assertEqual(result["validated"], {"valid": True})
        self.assertEqual(result["attempts"][0]["category"], "invalid_response")

    def test_model_overrides_are_used_without_logging_keys(self):
        seen = []

        def post(url, headers, payload, timeout):
            seen.append((url, headers, payload))
            raise provider.ProviderHTTPError(401)

        env = {
            "OPENROUTER_API_KEY": "router-test-secret",
            "OPENROUTER_MODEL": "provider/model-test",
        }
        with self.assertRaises(provider.ProviderUnavailable) as context:
            provider.chat_completion(
                [{"role": "user", "content": "Analyze TSLA"}],
                environ=env,
                post_json=post,
            )
        self.assertEqual(seen[0][2]["model"], "provider/model-test")
        self.assertNotIn("router-test-secret", json.dumps(context.exception.as_dict()))
        self.assertNotIn("router-test-secret", json.dumps(seen[0][2]))

    def test_endpoint_requires_https_and_expected_path(self):
        for endpoint in (
            "http://api.openai.com/v1/chat/completions",
            "https://user:pass@api.openai.com/v1/chat/completions",
            "https://api.openai.com/v1/other",
            "https://api.openai.com/v1/chat/completions?debug=1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    provider._validate_endpoint(endpoint)


if __name__ == "__main__":
    unittest.main()
