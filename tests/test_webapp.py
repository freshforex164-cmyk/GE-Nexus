import base64
import json
import os
import sys
import unittest
from unittest import mock

from _load import ROOT

sys.path.insert(0, os.path.join(ROOT, "trader-ai"))
import webapp


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = webapp.app.test_client()
        webapp._limiter = webapp.SlidingWindowLimiter()

    def test_health_and_status_do_not_expose_provider_secrets(self):
        secret = "do-not-return-this-provider-key"
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": secret,
                "OPENAI_MODEL": "unit-test-model",
            },
            clear=True,
        ):
            health = self.client.get("/api/health")
            response = self.client.get("/api/status")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json, {"ok": True, "service": "ge-nexus"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["engine"]["available"])
        self.assertTrue(response.json["providers"][1]["configured"])
        self.assertFalse(response.json["providers"][1]["connection_verified"])
        self.assertFalse(response.json["provider_checks_are_live"])
        self.assertFalse(response.json["analysis_history"]["persistent"])
        self.assertNotIn(secret, json.dumps(response.json))

    def test_render_requires_an_access_password(self):
        with self.assertRaisesRegex(RuntimeError, "GE_NEXUS_ACCESS_PASSWORD"):
            webapp._validate_deployment_configuration({"RENDER": "true"})
        webapp._validate_deployment_configuration(
            {"RENDER": "true", "GE_NEXUS_ACCESS_PASSWORD": "configured"}
        )

    def test_configured_password_protects_app_but_not_health_check(self):
        password = "test-only-access-password"
        basic = base64.b64encode(
            ("ge-nexus:" + password).encode("utf-8")
        ).decode("ascii")
        with mock.patch.dict(os.environ, {"GE_NEXUS_ACCESS_PASSWORD": password}):
            page = self.client.get("/")
            status = self.client.get("/api/status")
            health = self.client.get("/api/health")
            authorized_page = self.client.get(
                "/", headers={"Authorization": "Basic " + basic}
            )
            authorized_status = self.client.get(
                "/api/status", headers={"Authorization": "Basic " + basic}
            )
        self.assertEqual(page.status_code, 401)
        self.assertIn("Basic", page.headers["WWW-Authenticate"])
        self.assertEqual(status.status_code, 401)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(authorized_page.status_code, 200)
        self.assertEqual(authorized_status.status_code, 200)

    def test_scanner_route_uses_existing_validated_action_without_ai(self):
        expected = {
            "id": "a1",
            "tool": "scanner_daily",
            "status": "completed",
            "data": {
                "created_by": "trader-scanner",
                "source": "yahoo",
                "hits": [{"symbol": "AAPL", "setups": ["daily high"]}],
            },
        }
        with mock.patch.object(
            webapp.coordinator, "_run_actions", return_value=([expected], [])
        ) as run:
            response = self.client.post(
                "/api/scanner",
                json={"mode": "daily", "symbols": ["aapl"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["hits"][0]["symbol"], "AAPL")
        plan = run.call_args.args[0]
        self.assertEqual(plan["actions"][0]["tool"], "scanner_daily")
        self.assertEqual(plan["actions"][0]["arguments"]["symbols"], ["AAPL"])

    def test_scanner_rejects_invalid_symbols_and_modes(self):
        with mock.patch.object(webapp.coordinator, "_run_actions") as run:
            invalid_symbol = self.client.post(
                "/api/scanner",
                json={"mode": "daily", "symbols": ["AAPL; echo unsafe"]},
            )
            invalid_mode = self.client.post(
                "/api/scanner",
                json={"mode": "universe", "symbols": ["AAPL"]},
            )
        self.assertEqual(invalid_symbol.status_code, 400)
        self.assertEqual(invalid_mode.status_code, 400)
        run.assert_not_called()

    def test_analysis_route_calls_real_coordinator_contract(self):
        result = {
            "status": "complete",
            "planning_provider": "OpenAI",
            "actions": [],
            "interpretation": None,
        }
        with mock.patch.object(
            webapp.coordinator, "coordinate", return_value=result
        ) as coordinate:
            response = self.client.post(
                "/api/analyze", json={"request": "Check the exchange clock"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, result)
        coordinate.assert_called_once_with("Check the exchange clock")

    def test_unavailable_provider_is_reported_without_success_fallback(self):
        result = {
            "status": "unavailable",
            "error": {
                "category": "providers_unavailable",
                "message": "Use the Python tools independently.",
            },
            "actions": [],
            "interpretation": None,
        }
        with mock.patch.object(
            webapp.coordinator, "coordinate", return_value=result
        ):
            response = self.client.post(
                "/api/analyze", json={"request": "Analyze AAPL"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["status"], "unavailable")
        self.assertIsNone(response.json["interpretation"])

    def test_analysis_request_validation_and_rate_limit(self):
        with mock.patch.object(
            webapp.coordinator,
            "coordinate",
            return_value={"status": "clarification", "actions": []},
        ) as coordinate:
            missing = self.client.post("/api/analyze", json={"request": " "})
            wrong_content_type = self.client.post("/api/analyze", data="{}")
            for _ in range(webapp.MAX_REQUESTS_PER_WINDOW):
                self.client.post("/api/analyze", json={"request": "Check clock"})
            too_many = self.client.post(
                "/api/analyze", json={"request": "Check clock"}
            )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(wrong_content_type.status_code, 415)
        self.assertEqual(too_many.status_code, 429)
        self.assertGreater(int(too_many.headers["Retry-After"]), 0)
        self.assertEqual(
            coordinate.call_count, webapp.MAX_REQUESTS_PER_WINDOW
        )

    def test_cross_origin_api_calls_are_rejected(self):
        response = self.client.get(
            "/api/status", headers={"Origin": "https://attacker.example"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["error"]["category"], "origin_rejected")

    def test_exchange_clock_uses_existing_python_tool(self):
        clock = {
            "id": "a1",
            "tool": "alerts_clock",
            "status": "completed",
            "data": {"open": False},
        }
        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(clock, [])
        ) as execute:
            response = self.client.get("/api/clock")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"], {"open": False})
        execute.assert_called_once_with(
            "alerts_clock", {}, "Check the exchange clock"
        )

    def test_service_worker_is_root_scoped_and_api_responses_are_not_cached(self):
        with mock.patch.object(
            webapp, "_execute_validated_action",
            return_value=({"id": "a1", "tool": "alerts_clock", "status": "completed"}, []),
        ):
            worker = self.client.get("/sw.js")
            api = self.client.get("/api/clock")
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(api.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
