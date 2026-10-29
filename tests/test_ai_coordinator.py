import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from _load import ROOT

sys.path.insert(0, os.path.join(ROOT, "trader-ai"))
import coordinator
import provider


def action(tool, arguments=None, dependencies=None):
    return {
        "id": "a1",
        "tool": tool,
        "arguments": arguments or {},
        "depends_on": dependencies or [],
        "expected_output": coordinator.TOOL_CATALOG[tool]["output"],
    }


def plan(actions, clarification=None):
    for index, item in enumerate(actions):
        item["id"] = "a%d" % (index + 1)
    return {
        "version": 1,
        "intent": "test analysis",
        "actions": actions,
        "clarification": clarification,
    }


def provider_result(content):
    return {"provider": "OpenAI", "model": "test-model", "content": content, "attempts": []}


class PlanValidationTests(unittest.TestCase):
    def test_accepts_explicit_symbol_and_normalizes_allowed_arguments(self):
        raw = plan([action("scanner_daily", {"symbols": ["TSLA", "AAPL"], "min_price": 2})])
        checked = coordinator.validate_plan(raw, "Scan TSLA and AAPL for setups")
        self.assertEqual(checked["actions"][0]["arguments"], {
            "symbols": ["TSLA", "AAPL"], "min_price": 2.0
        })

    def test_requires_symbols_to_appear_explicitly_in_request(self):
        with self.assertRaisesRegex(coordinator.PlanError, "not explicitly supplied"):
            coordinator.validate_plan(
                plan([action("entry_check", {"symbol": "TSLA"})]),
                "Analyze Tesla",
            )

    def test_rejects_malformed_symbols_and_shell_metacharacters(self):
        for symbol in ("TSLA;rm", "TSLA && echo", "../../scan.py"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(coordinator.PlanError):
                    coordinator.validate_plan(
                        plan([action("entry_check", {"symbol": symbol})]),
                        "Analyze " + symbol,
                    )

    def test_rejects_unknown_tools_and_broker_actions(self):
        for tool in ("shell", "trader-broker", "broker_place_order"):
            with self.subTest(tool=tool):
                item = action("alerts_clock")
                item["tool"] = tool
                with self.assertRaisesRegex(coordinator.PlanError, "unsupported tool"):
                    coordinator.validate_plan(plan([item]), "Check TSLA")

    def test_rejects_unknown_argument_fields(self):
        with self.assertRaisesRegex(coordinator.PlanError, "unsupported argument"):
            coordinator.validate_plan(
                plan([action("alerts_clock", {"command": "python -c bad"})]),
                "Check TSLA",
            )

    def test_rejects_unsupported_level_setup_without_level(self):
        with self.assertRaisesRegex(coordinator.PlanError, "requires a supplied level"):
            coordinator.validate_plan(
                plan([action("entry_check", {"symbol": "TSLA", "setup": "level"})]),
                "Check TSLA",
            )

    def test_accepts_level_setup_with_explicit_numeric_level(self):
        result = coordinator.validate_plan(
            plan([action("entry_check", {
                "symbol": "TSLA", "setup": "level", "level": 350.25,
            })]),
            "Check TSLA above the level",
        )
        self.assertEqual(result["actions"][0]["arguments"]["level"], 350.25)

    def test_exit_plan_requires_complete_safe_inputs_or_saved_entry(self):
        saved = coordinator.validate_plan(plan([action("exit_plan")]), "Plan TSLA exit")
        self.assertEqual(saved["actions"][0]["arguments"], {})
        with self.assertRaisesRegex(coordinator.PlanError, "require entry and stop"):
            coordinator.validate_plan(
                plan([action("exit_plan", {"symbol": "TSLA"})]),
                "Plan TSLA exit",
            )

    def test_exit_plan_validates_range_and_stop_prerequisites(self):
        with self.assertRaisesRegex(coordinator.PlanError, "requires or_high and or_low"):
            coordinator.validate_plan(
                plan([action("exit_plan", {
                    "symbol": "TSLA", "entry": 100, "stop": "or-mid",
                })]),
                "Plan TSLA exit",
            )
        valid = coordinator.validate_plan(
            plan([action("exit_plan", {
                "symbol": "TSLA", "entry": 100, "setup": "orb",
                "or_high": 102, "or_low": 98, "stop": "or-mid", "target": "2R",
            })]),
            "Plan TSLA exit",
        )
        self.assertEqual(valid["actions"][0]["arguments"]["stop"], "or-mid")

    def test_dependencies_must_reference_prior_unique_actions(self):
        items = [
            action("alerts_clock"),
            action("risk_check", dependencies=["a3"]),
        ]
        with self.assertRaisesRegex(coordinator.PlanError, "earlier action"):
            coordinator.validate_plan(plan(items), "Check TSLA risk")

    def test_unhashable_dependency_is_rejected_as_a_plan_error(self):
        items = [action("alerts_clock"), action("risk_check", dependencies=[["a1"]])]
        with self.assertRaisesRegex(coordinator.PlanError, "earlier action"):
            coordinator.validate_plan(plan(items), "Check the exchange clock")

    def test_generated_file_consumers_require_prior_action_dependencies(self):
        actions = [
            action("exit_plan", {
                "symbol": "TSLA", "entry": 100, "stop": "price:99",
            }),
            action("alerts_quote", {"symbols": ["TSLA"]}),
            action("budget_size", dependencies=["a1"]),
        ]
        with self.assertRaisesRegex(coordinator.PlanError, "prior alerts_quote"):
            coordinator.validate_plan(
                plan(actions),
                "Plan TSLA exit, check the TSLA quote, and size it",
            )

    def test_rejects_wrong_ids_output_types_and_excess_calls(self):
        wrong_type = plan([action("alerts_clock")])
        wrong_type["actions"][0]["expected_output"] = "text"
        with self.assertRaisesRegex(coordinator.PlanError, "incorrect expected output"):
            coordinator.validate_plan(wrong_type, "Check the exchange clock")

        too_many = plan([action("alerts_clock") for _ in range(5)])
        with self.assertRaisesRegex(coordinator.PlanError, "at most"):
            coordinator.validate_plan(too_many, "Check the exchange clock")

    def test_accepts_clear_clarification_without_actions(self):
        checked = coordinator.validate_plan(
            plan([], "Please provide the uppercase ticker symbol."),
            "Analyze Tesla",
        )
        self.assertEqual(checked["clarification"], "Please provide the uppercase ticker symbol.")
        self.assertEqual(checked["actions"], [])

    def test_rejects_non_json_and_extra_plan_fields(self):
        with self.assertRaisesRegex(coordinator.PlanError, "valid JSON"):
            coordinator.validate_plan("not json", "Analyze TSLA")
        document = plan([action("alerts_clock")])
        document["shell"] = "not permitted"
        with self.assertRaisesRegex(coordinator.PlanError, "exactly"):
            coordinator.validate_plan(document, "Check the exchange clock")

    def test_request_secrets_are_redacted_before_provider_prompt(self):
        messages = coordinator.planner_messages(
            "Analyze TSLA. API_KEY=sk-proj-12345678901234567890"
        )
        self.assertIn("TSLA", messages[1]["content"])
        self.assertNotIn("12345678901234567890", messages[1]["content"])
        self.assertIn("[REDACTED]", messages[1]["content"])


class ExecutionTests(unittest.TestCase):
    def test_builder_uses_fixed_script_and_only_validated_arguments(self):
        checked = coordinator.validate_plan(
            plan([action("scanner_daily", {"symbols": ["TSLA"]})]),
            "Scan TSLA",
        )
        command = coordinator._build_command(checked["actions"][0])
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], os.path.join(ROOT, "trader-scanner", "scan.py"))
        self.assertEqual(command[2:], ["daily", "--symbols", "TSLA", "--json"])
        self.assertNotIn("--write", command)

    def test_subprocess_uses_temp_working_directory_and_drops_provider_keys(self):
        checked = coordinator.validate_plan(
            plan([action("alerts_clock")]),
            "Check the exchange clock",
        )
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"open":false}', stderr="",
        )
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-secret",
                "GROK_API_KEY": "test-grok-secret",
            }, clear=False):
                with mock.patch.object(coordinator.subprocess, "run", return_value=completed) as run:
                    result = coordinator._run_one(
                        checked["actions"][0], coordinator.Path(temp),
                        coordinator.time.monotonic() + 10,
                    )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data"], {"open": False})
        call = run.call_args
        self.assertIs(call.kwargs["shell"], False)
        self.assertEqual(call.kwargs["cwd"], temp)
        self.assertNotIn("OPENAI_API_KEY", call.kwargs["env"])
        self.assertNotIn("GROK_API_KEY", call.kwargs["env"])
        self.assertTrue(all(isinstance(part, str) for part in call.args[0]))

    def test_nonzero_exit_and_invalid_json_are_not_success(self):
        checked = coordinator.validate_plan(
            plan([action("alerts_clock")]),
            "Check the exchange clock",
        )
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(
                coordinator.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "not json", ""),
            ):
                result = coordinator._run_one(
                    checked["actions"][0], coordinator.Path(temp),
                    coordinator.time.monotonic() + 10,
                )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["category"], "invalid_json_output")

    def test_timeout_and_too_large_outputs_fail_safely(self):
        checked = coordinator.validate_plan(
            plan([action("alerts_clock")]),
            "Check the exchange clock",
        )
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(
                coordinator.subprocess, "run", side_effect=subprocess.TimeoutExpired("cmd", 1)
            ):
                timed_out = coordinator._run_one(
                    checked["actions"][0], coordinator.Path(temp),
                    coordinator.time.monotonic() + 10,
                )
            with mock.patch.object(
                coordinator.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, " " * (coordinator.MAX_TOOL_OUTPUT_BYTES + 1), ""
                ),
            ):
                too_large = coordinator._run_one(
                    checked["actions"][0], coordinator.Path(temp),
                    coordinator.time.monotonic() + 10,
                )
        self.assertEqual(timed_out["category"], "tool_timeout")
        self.assertEqual(too_large["category"], "tool_output_too_large")

    def test_live_pipe_capture_is_bounded_for_stdout_and_stderr(self):
        def noisy_run(command, **kwargs):
            os.write(kwargs["stdout"], b"x" * (coordinator.MAX_TOOL_OUTPUT_BYTES + 1))
            os.write(kwargs["stderr"], b"y" * 128)
            return subprocess.CompletedProcess(command, 0, None, None)

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(coordinator.subprocess, "run", side_effect=noisy_run):
                completed, stdout, stderr, overflow = coordinator._run_bounded_subprocess(
                    [sys.executable, "-c", "pass"],
                    coordinator.Path(temp),
                    {},
                    5,
                )
        self.assertEqual(completed.returncode, 0)
        self.assertTrue(overflow)
        self.assertLessEqual(
            len(stdout) + len(stderr), coordinator.MAX_TOOL_OUTPUT_BYTES
        )

    def test_failed_prerequisite_skips_dependent_action(self):
        checked = coordinator.validate_plan(
            plan([
                action("entry_check", {"symbol": "TSLA"}),
                action("exit_plan", dependencies=["a1"]),
            ]),
            "Check TSLA entry and plan an exit",
        )
        failed = subprocess.CompletedProcess([], 1, "", "prerequisite missing")
        with mock.patch.object(coordinator.subprocess, "run", return_value=failed) as run:
            results, _ = coordinator._run_actions(
                checked, coordinator.time.monotonic() + 20
            )
        self.assertEqual(run.call_count, 1)
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[1]["status"], "skipped")
        self.assertEqual(results[1]["category"], "prerequisite_failed")

    def test_nontriggered_entry_does_not_run_dependent_exit_plan(self):
        checked = coordinator.validate_plan(
            plan([
                action("entry_check", {"symbol": "TSLA"}),
                action("exit_plan", dependencies=["a1"]),
            ]),
            "Check TSLA entry and plan an exit",
        )
        completed = subprocess.CompletedProcess(
            [], 0, json.dumps({"status": "waiting"}), "",
        )
        with mock.patch.object(
            coordinator.subprocess, "run", return_value=completed
        ) as run:
            results, _ = coordinator._run_actions(
                checked, coordinator.time.monotonic() + 20
            )
        self.assertEqual(run.call_count, 1)
        self.assertTrue(results[0]["dependency_ready"] is False)
        self.assertEqual(results[1]["category"], "prerequisite_failed")

    def test_interpretation_schema_is_strict(self):
        valid = json.dumps({
            "summary": "No trigger reported.",
            "reported_facts": ["The entry tool reports waiting."],
            "interpretation": "No conclusion beyond that result.",
            "limitations": ["The data may be delayed."],
        })
        self.assertEqual(coordinator._parse_interpretation(valid)["summary"], "No trigger reported.")
        with self.assertRaises(ValueError):
            coordinator._parse_interpretation('{"summary":"missing other fields"}')


class CoordinatorFlowTests(unittest.TestCase):
    def test_coordinator_plans_executes_and_interprets_without_leaking_request_secret(self):
        plan_content = json.dumps(plan([action("alerts_clock")]))
        interpretation = json.dumps({
            "summary": "The exchange clock result is available.",
            "reported_facts": ["Python reported that the exchange is closed."],
            "interpretation": "This is only a clock status.",
            "limitations": [],
        })
        completed = subprocess.CompletedProcess([], 0, '{"open":false}', "")
        request = "Check the exchange clock. API_KEY=sk-proj-12345678901234567890"
        with mock.patch.object(
            coordinator.provider,
            "chat_completion",
            side_effect=[provider_result(plan_content), provider_result(interpretation)],
        ) as call_provider:
            with mock.patch.object(
                coordinator.subprocess, "run", return_value=completed
            ) as run:
                result = coordinator.coordinate(request)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["actions"][0]["data"], {"open": False})
        self.assertEqual(call_provider.call_count, 2)
        self.assertNotIn("12345678901234567890", call_provider.call_args_list[0].args[0][1]["content"])
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_no_provider_keeps_cli_results_path_available_without_running_tools(self):
        with mock.patch.object(
            coordinator.provider,
            "chat_completion",
            side_effect=provider.ProviderUnavailable([
                {"provider": "OpenRouter", "category": "missing_key"}
            ]),
        ):
            with mock.patch.object(coordinator.subprocess, "run") as run:
                result = coordinator.coordinate("Analyze TSLA")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("Python analysis tools independently", result["error"]["message"])
        run.assert_not_called()

    def test_interpreter_failure_preserves_python_outputs(self):
        plan_content = json.dumps(plan([action("alerts_clock")]))
        completed = subprocess.CompletedProcess([], 0, '{"open":false}', "")
        with mock.patch.object(
            coordinator.provider,
            "chat_completion",
            side_effect=[
                provider_result(plan_content),
                provider.ProviderUnavailable([
                    {"provider": "OpenRouter", "category": "missing_key"}
                ]),
            ],
        ):
            with mock.patch.object(
                coordinator.subprocess, "run", return_value=completed
            ):
                result = coordinator.coordinate("Check the exchange clock")
        self.assertEqual(result["status"], "results_available")
        self.assertEqual(result["actions"][0]["data"], {"open": False})
        self.assertIsNone(result["interpretation"])
        rendered = coordinator.render_result(result)
        self.assertIn('"open": false', rendered)

    def test_clarification_does_not_run_tools_or_make_second_model_call(self):
        content = json.dumps(plan([], "Please provide the uppercase ticker symbol."))
        with mock.patch.object(
            coordinator.provider, "chat_completion", return_value=provider_result(content)
        ) as call_provider:
            with mock.patch.object(coordinator.subprocess, "run") as run:
                result = coordinator.coordinate("Analyze Tesla")
        self.assertEqual(result["status"], "clarification")
        self.assertEqual(call_provider.call_count, 1)
        run.assert_not_called()

    def test_model_plan_with_broker_tool_is_rejected(self):
        bad = plan([action("alerts_clock")])
        bad["actions"][0]["tool"] = "trader-broker"
        with mock.patch.object(
            coordinator.provider, "chat_completion", return_value=provider_result(json.dumps(bad))
        ):
            with mock.patch.object(coordinator.subprocess, "run") as run:
                result = coordinator.coordinate("Check the exchange clock")
        self.assertEqual(result["status"], "plan_rejected")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
