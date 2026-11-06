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
        with webapp._research_contexts_lock:
            webapp._research_contexts.clear()

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
        self.assertTrue(response.json["providers"][1]["key_configured"])
        self.assertTrue(response.json["providers"][1]["model_configured"])
        self.assertFalse(response.json["providers"][1]["connection_verified"])
        self.assertFalse(response.json["provider_checks_are_live"])
        self.assertEqual(
            response.json["analysis_history"]["persistent"], "browser_local_storage"
        )
        self.assertFalse(response.json["analysis_history"]["server_database"])
        self.assertNotIn(secret, json.dumps(response.json))

    def test_render_requires_an_access_password(self):
        with self.assertRaisesRegex(RuntimeError, "GE_NEXUS_ACCESS_PASSWORD"):
            webapp._validate_deployment_configuration({"RENDER": "true"})
        webapp._validate_deployment_configuration(
            {"RENDER": "true", "GE_NEXUS_ACCESS_PASSWORD": "configured"}
        )

    def test_app_remains_public_when_a_deployment_password_is_configured(self):
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
        self.assertEqual(page.status_code, 200)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(authorized_page.status_code, 200)
        self.assertEqual(authorized_status.status_code, 200)

    def test_scanner_route_uses_existing_validated_action_and_preserves_results(self):
        expected = {
            "id": "a1",
            "tool": "scanner_daily",
            "status": "completed",
            "data": {
                "created_by": "trader-scanner",
                "source": "yahoo",
                "universe": 1,
                "scanned": 1,
                "hits": [{"symbol": "AAPL", "setups": ["daily high"]}],
            },
        }
        with mock.patch.object(
            webapp.coordinator, "_run_actions", return_value=([expected], [])
        ) as run, mock.patch.object(
            webapp, "_interpret_action", side_effect=lambda result, _text: result
        ) as interpret:
            response = self.client.post(
                "/api/scanner",
                json={"mode": "daily", "symbols": ["aapl"]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["hits"][0]["symbol"], "AAPL")
        self.assertEqual(response.json["requested_symbols"], ["AAPL"])
        interpret.assert_called_once()
        plan = run.call_args.args[0]
        self.assertEqual(plan["actions"][0]["tool"], "scanner_daily")
        self.assertEqual(plan["actions"][0]["arguments"]["symbols"], ["AAPL"])

    def test_scanner_uses_default_universe_and_exposes_supported_assets(self):
        response = self.client.get("/api/universe")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["default_symbols"], list(webapp.DEFAULT_SCAN_SYMBOLS))
        self.assertEqual(response.json["max_symbols"], 20)
        self.assertIn("U.S.-listed common stocks", response.json["market"]["supported"])
        self.assertIn("forex", response.json["market"]["unsupported"])
        self.assertNotIn("NASDAQ", response.json["default_symbols"])

        expected = {
            "id": "a1",
            "tool": "scanner_daily",
            "status": "completed",
            "data": {"created_by": "trader-scanner", "mode": "daily", "universe": 1, "scanned": 1, "hits": []},
        }
        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(expected, [])
        ) as execute, mock.patch.object(
            webapp, "_interpret_action", side_effect=lambda result, _text: result
        ):
            scan = self.client.post("/api/scanner", json={"mode": "daily"})
        self.assertEqual(scan.status_code, 200)
        self.assertEqual(scan.json["requested_symbols"], list(webapp.DEFAULT_SCAN_SYMBOLS))
        self.assertEqual(
            execute.call_args.args[1]["symbols"], list(webapp.DEFAULT_SCAN_SYMBOLS)
        )

    def test_scanner_auto_interprets_only_completed_tool_results(self):
        successful = {
            "id": "a1", "tool": "scanner_daily", "status": "completed",
            "data": {"created_by": "trader-scanner", "mode": "daily", "universe": 1, "scanned": 1, "hits": []},
        }
        failed = {"id": "a1", "tool": "scanner_daily", "status": "failed"}
        interpretation = {
            "summary": "No matching setups.",
            "reported_facts": [],
            "interpretation": "No scan candidates were returned.",
            "limitations": [],
        }
        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(successful.copy(), [])
        ), mock.patch.object(
            webapp.coordinator, "_interpret",
            return_value=(interpretation, None, "OpenAI"),
        ) as interpret:
            response = self.client.post(
                "/api/scanner", json={"mode": "daily", "symbols": ["AAPL"]}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["interpretation"], interpretation)
        self.assertEqual(response.json["interpretation_provider"], "OpenAI")
        interpret.assert_called_once()

        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(failed, [])
        ), mock.patch.object(webapp, "_interpret_action") as interpret_failed:
            response = self.client.post(
                "/api/scanner", json={"mode": "daily", "symbols": ["AAPL"]}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["scan_summary"]["status"], "failed")
        interpret_failed.assert_not_called()

    def test_scanner_rejects_invalid_symbols_and_modes(self):
        with mock.patch.object(webapp, "_execute_validated_action") as run:
            invalid_symbol = self.client.post(
                "/api/scanner",
                json={"mode": "daily", "symbols": ["AAPL; echo unsafe"]},
            )
            invalid_mode = self.client.post(
                "/api/scanner",
                json={"mode": "universe", "symbols": ["AAPL"]},
            )
            too_many = self.client.post(
                "/api/scanner",
                json={"mode": "daily", "symbols": ["AAPL"] * 21},
            )
        self.assertEqual(invalid_symbol.status_code, 400)
        self.assertEqual(invalid_mode.status_code, 400)
        self.assertEqual(too_many.status_code, 400)
        run.assert_not_called()

    def test_instrument_analysis_uses_existing_quote_tool_and_interpretation(self):
        quote = {"id": "a1", "tool": "alerts_quote", "status": "completed", "data": "AAPL $1.00"}
        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(quote, [])
        ) as execute, mock.patch.object(
            webapp, "_interpret_action", side_effect=lambda result, _text: result
        ) as interpret:
            response = self.client.post("/api/instrument", json={"symbol": "aapl"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["symbol"], "AAPL")
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0], "alerts_quote")
        interpret.assert_called_once()

    def test_instrument_rejects_exchange_names_and_invalid_tickers(self):
        with mock.patch.object(webapp, "_execute_validated_action") as execute:
            response = self.client.post("/api/instrument", json={"symbol": "NASDAQ"})
        self.assertEqual(response.status_code, 400)
        execute.assert_not_called()

    def test_risk_route_requires_user_supplied_orders_and_limits(self):
        with mock.patch.object(webapp, "_has_regular_runtime_file", return_value=False):
            response = self.client.post("/api/risk", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"]["category"], "risk_inputs_missing")

        result = {"id": "a1", "tool": "risk_check", "status": "completed", "data": {"passed": 1}}
        with mock.patch.object(webapp, "_has_regular_runtime_file", return_value=True), \
             mock.patch.object(
                 webapp, "_execute_validated_action", return_value=(result, [])
             ) as execute, mock.patch.object(
                 webapp, "_interpret_action", side_effect=lambda result, _text: result
             ):
            response = self.client.post("/api/risk", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"], {"passed": 1})
        execute.assert_called_once_with(
            "risk_check", {}, "Check the configured proposed orders against the saved risk limits"
        )

    def test_provider_check_reports_success_only_after_a_real_completion(self):
        response_value = {
            "provider": "OpenRouter", "model": "test-model", "content": "OK"
        }
        with mock.patch.object(
            webapp.coordinator.provider, "chat_completion", return_value=response_value
        ) as request:
            response = self.client.post("/api/providers/check", json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["verified"])
        self.assertEqual(response.json["provider"], "OpenRouter")
        request.assert_called_once()

        unavailable = webapp.coordinator.provider.ProviderUnavailable(
            [{"provider": "OpenRouter", "category": "missing_model"}]
        )
        with mock.patch.object(
            webapp.coordinator.provider, "chat_completion", side_effect=unavailable
        ):
            response = self.client.post("/api/providers/check", json={})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json["verified"])
        self.assertEqual(response.json["attempts"][0]["category"], "missing_model")

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

    def test_auto_scan_uses_exchange_clock_and_avoids_unsupported_intraday_windows(self):
        open_clock = {
            "status": "completed",
            "data": {
                "calendar_known": True,
                "open": True,
                "now_new_york": "2026-10-19 10:15",
                "opens": "09:30",
                "closes": "16:00",
            },
        }
        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(open_clock, [])
        ):
            self.assertEqual(webapp._automatic_scan_mode()[0], "orb")
        clocks_requiring_daily = (
            {"status": "failed", "data": {}},
            {"status": "completed", "data": {"calendar_known": True, "open": False}},
            {"status": "completed", "data": {"calendar_known": False, "open": True}},
            {
                "status": "completed",
                "data": {
                    "calendar_known": True,
                    "open": True,
                    "now_new_york": "2026-10-19 09:40",
                    "opens": "09:30",
                    "closes": "16:00",
                },
            },
        )
        for clock in clocks_requiring_daily:
            with mock.patch.object(
                webapp, "_execute_validated_action", return_value=(clock, [])
            ):
                self.assertEqual(webapp._automatic_scan_mode()[0], "daily")

    def test_research_question_uses_server_retained_python_evidence(self):
        scan_result = {
            "id": "a1",
            "tool": "scanner_daily",
            "status": "completed",
            "data": {
                "created_by": "trader-scanner",
                "mode": "daily",
                "universe": 1,
                "scanned": 1,
                "source": "yahoo",
                "hits": [
                    {"symbol": "AAPL", "setups": ["breakout_20d"], "price": 200.0}
                ],
            },
        }
        with mock.patch.object(
            webapp, "_execute_validated_action", return_value=(scan_result, [])
        ), mock.patch.object(
            webapp, "_interpret_action", side_effect=lambda result, _text: result
        ):
            scan = self.client.post(
                "/api/scanner", json={"mode": "daily", "symbols": ["AAPL"]}
            )
        self.assertEqual(scan.status_code, 200)
        research_id = scan.json["research_id"]
        self.assertTrue(research_id)

        answer = {
            "answer": "The result reports a 20-day breakout.",
            "limitations": ["This is a research observation, not a recommendation."],
            "provider": "OpenAI",
            "model": "test-model",
            "attempts": [],
        }
        with mock.patch.object(
            webapp.coordinator, "answer_research_question", return_value=answer
        ) as ask:
            response = self.client.post(
                "/api/research/ask",
                json={"research_id": research_id, "question": "What did AAPL match?"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["answer"], answer["answer"])
        evidence = ask.call_args.args[1]
        self.assertEqual(evidence["data"]["hits"][0]["symbol"], "AAPL")

    def test_research_question_reports_expired_context_without_calling_ai(self):
        with mock.patch.object(
            webapp.coordinator, "answer_research_question"
        ) as ask:
            response = self.client.post(
                "/api/research/ask",
                json={"research_id": "A" * 32, "question": "What is the entry price?"},
            )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(
            response.json["error"]["category"], "research_context_expired"
        )
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
