#!/usr/bin/env python3
"""GE Nexus optional AI coordinator; the existing Python CLIs remain standalone."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import provider


ROOT = Path(__file__).resolve().parents[1]
MAX_REQUEST_CHARS = 4000
MAX_ACTIONS = 4
MAX_TOOL_OUTPUT_BYTES = 64 * 1024
MAX_MODEL_ACTION_OUTPUT_CHARS = 6000
MAX_TOTAL_SECONDS = 120
TOOL_TIMEOUT_SECONDS = 25
RUNTIME_FILE_LIMIT = 1024 * 1024
API_KEY_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GROK_API_KEY",
    "DEEPSEEK_API_KEY",
)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
PRICE_STOP_RE = re.compile(r"^(?:price|pct|atr):([0-9]+(?:\.[0-9]+)?)$", re.I)
TARGET_RE = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?R|price:[0-9]+(?:\.[0-9]+)?)$", re.I)
EXPLICIT_TICKER_RE = re.compile(
    r"(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?)(?![A-Za-z0-9])"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_ -]?key|secret|access[_ -]?token|password|authorization)"
    r"(\s*[:=]\s*)([\"']?)[^\s,;\"']+"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
COMMON_KEY_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|xai-[A-Za-z0-9_-]{16,}|"
    r"gsk_[A-Za-z0-9_-]{16,})\b"
)


TOOL_CATALOG = {
    "scanner_daily": {
        "description": "Run the existing daily setup scan for explicit ticker symbols.",
        "arguments": '{"symbols":[...], "min_price": number?}',
        "output": "json",
    },
    "scanner_orb": {
        "description": "Run the existing opening-range scan for explicit ticker symbols.",
        "arguments": '{"symbols":[...], "at":"HH:MM"?, "or_minutes": number?}',
        "output": "json",
    },
    "news": {
        "description": "Retrieve headline news, without SEC requests or state-file updates.",
        "arguments": '{"symbols":[...]?, "hours": number?}',
        "output": "json",
    },
    "entry_check": {
        "description": "Check one ticker against the existing entry rule; save only in a temporary workspace.",
        "arguments": '{"symbol":"...", "setup":"orb|level"?, "interval":"5m|1m"?, "level": number?, "min_volume_x": number?}',
        "output": "json",
    },
    "exit_plan": {
        "description": "Calculate an exit plan from a saved entry or fully supplied entry and stop values.",
        "arguments": '{"symbol":"..."?, "entry":number?, "setup":"orb|level"?, "or_high":number?, "or_low":number?, "stop":"..."?, "target":"..."?}',
        "output": "json",
    },
    "timemachine": {
        "description": "Run the existing historical simulation from trader/plan.json in the temporary workspace.",
        "arguments": "{}",
        "output": "json",
    },
    "alerts_clock": {
        "description": "Check the exchange clock without retrieving quotes.",
        "arguments": "{}",
        "output": "json",
    },
    "alerts_quote": {
        "description": "Retrieve quotes for explicit tickers; any saved price file stays temporary.",
        "arguments": '{"symbols":[...]}',
        "output": "text",
    },
    "analyst": {
        "description": "Retrieve available SEC XBRL facts for explicit tickers, without filing links or saved metrics.",
        "arguments": '{"symbols":[...], "quarters": number?}',
        "output": "json",
    },
    "budget_size": {
        "description": "Calculate a proposed position size from saved plan/config inputs; output remains temporary.",
        "arguments": "{}",
        "output": "json",
    },
    "risk_check": {
        "description": "Check saved proposed orders and configured limits without saving a verdict.",
        "arguments": "{}",
        "output": "json",
    },
}

SCRIPT_PATHS = {
    "scanner_daily": "trader-scanner/scan.py",
    "scanner_orb": "trader-scanner/scan.py",
    "news": "trader-news/news.py",
    "entry_check": "trader-entry/entry.py",
    "exit_plan": "trader-exit/exit.py",
    "timemachine": "trader-timemachine/timemachine.py",
    "alerts_clock": "trader-alerts/alerts.py",
    "alerts_quote": "trader-alerts/alerts.py",
    "analyst": "trader-analyst/facts.py",
    "budget_size": "trader-budget/size.py",
    "risk_check": "trader-risk/check.py",
}

RUNTIME_INPUTS = (
    "config.json",
    "portfolio.json",
    "watchlist.json",
    "entry.json",
    "plan.json",
    "orders.json",
    "prices.json",
    "universe.txt",
    "alerts.json",
)
RUNTIME_STATE_INPUTS = ("alerts.json", "broker.json", "risk.json", "news.json", "pelosi.json")


class PlanError(Exception):
    pass


def redact_sensitive(text):
    text = SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", str(text))
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    return COMMON_KEY_RE.sub("[REDACTED]", text)


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError("%s must be a number" % name)
    number = float(value)
    if not math.isfinite(number):
        raise PlanError("%s must be finite" % name)
    if minimum is not None and number < minimum:
        raise PlanError("%s is below its allowed range" % name)
    if maximum is not None and number > maximum:
        raise PlanError("%s is above its allowed range" % name)
    return number


def _symbols(value, request, name="symbols", required=True, maximum=12):
    if value is None and not required:
        return []
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise PlanError("%s must be a list of 1 to %d ticker symbols" % (name, maximum))
    request_symbols = set(EXPLICIT_TICKER_RE.findall(request))
    checked = []
    for symbol in value:
        if not isinstance(symbol, str):
            raise PlanError("ticker symbols must be strings")
        symbol = symbol.strip().upper()
        if not TICKER_RE.match(symbol):
            raise PlanError("invalid ticker symbol")
        if symbol not in request_symbols:
            raise PlanError(
                "ticker %s was not explicitly supplied in uppercase in the request" % symbol
            )
        if symbol in checked:
            raise PlanError("duplicate ticker symbol")
        checked.append(symbol)
    return checked


def _check_fields(arguments, allowed, required=()):
    if not isinstance(arguments, dict):
        raise PlanError("action arguments must be an object")
    unknown = set(arguments) - set(allowed)
    missing = set(required) - set(arguments)
    if unknown:
        raise PlanError("unsupported argument field: %s" % sorted(unknown)[0])
    if missing:
        raise PlanError("missing required argument field: %s" % sorted(missing)[0])


def _validate_action_arguments(tool, arguments, request):
    if tool in ("scanner_daily", "scanner_orb"):
        if tool == "scanner_daily":
            _check_fields(arguments, ("symbols", "min_price"), ("symbols",))
            result = {"symbols": _symbols(arguments["symbols"], request, maximum=20)}
            if "min_price" in arguments:
                result["min_price"] = _number(
                    arguments["min_price"], "min_price", minimum=0, maximum=100000
                )
            return result
        _check_fields(arguments, ("symbols", "at", "or_minutes"), ("symbols",))
        result = {"symbols": _symbols(arguments["symbols"], request, maximum=20)}
        if "at" in arguments:
            if not isinstance(arguments["at"], str) or not TIME_RE.match(arguments["at"]):
                raise PlanError("at must use HH:MM")
            result["at"] = arguments["at"]
        if "or_minutes" in arguments:
            value = arguments["or_minutes"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 5 or value > 60 or value % 5:
                raise PlanError("or_minutes must be a multiple of 5 from 5 through 60")
            result["or_minutes"] = value
        return result

    if tool == "news":
        _check_fields(arguments, ("symbols", "hours"))
        result = {}
        if "symbols" in arguments:
            result["symbols"] = _symbols(
                arguments["symbols"], request, required=False, maximum=12
            )
        if "hours" in arguments:
            result["hours"] = _number(arguments["hours"], "hours", minimum=1, maximum=720)
        return result

    if tool == "entry_check":
        _check_fields(
            arguments,
            ("symbol", "setup", "interval", "level", "min_volume_x"),
            ("symbol",),
        )
        symbol = _symbols([arguments["symbol"]], request, name="symbol", maximum=1)[0]
        setup = arguments.get("setup", "orb")
        interval = arguments.get("interval", "5m")
        if setup not in ("orb", "level"):
            raise PlanError("setup must be orb or level")
        if interval not in ("1m", "5m"):
            raise PlanError("interval must be 1m or 5m")
        result = {"symbol": symbol, "setup": setup, "interval": interval}
        if setup == "level":
            if "level" not in arguments:
                raise PlanError("a level setup requires a supplied level")
            result["level"] = _number(arguments["level"], "level", minimum=0.0001)
        elif "level" in arguments:
            raise PlanError("level is only valid for a level setup")
        if "min_volume_x" in arguments:
            result["min_volume_x"] = _number(
                arguments["min_volume_x"], "min_volume_x", minimum=0, maximum=20
            )
        return result

    if tool == "exit_plan":
        allowed = ("symbol", "entry", "setup", "or_high", "or_low", "stop", "target")
        _check_fields(arguments, allowed)
        if not arguments:
            return {}
        if "symbol" not in arguments:
            raise PlanError("explicit exit inputs require a symbol")
        if "entry" not in arguments or "stop" not in arguments:
            raise PlanError("explicit exit inputs require entry and stop")
        result = {
            "symbol": _symbols([arguments["symbol"]], request, name="symbol", maximum=1)[0],
            "entry": _number(arguments["entry"], "entry", minimum=0.0001),
            "stop": arguments["stop"],
        }
        setup = arguments.get("setup", "orb")
        if setup not in ("orb", "level"):
            raise PlanError("setup must be orb or level")
        result["setup"] = setup
        for field in ("or_high", "or_low"):
            if field in arguments:
                result[field] = _number(arguments[field], field, minimum=0.0001)
        stop = result["stop"]
        if not isinstance(stop, str):
            raise PlanError("stop must use an existing exit-planner stop form")
        if stop in ("or-mid", "or-low"):
            if setup != "orb":
                raise PlanError("opening-range stops require an orb setup")
            if stop == "or-mid" and not all(k in result for k in ("or_high", "or_low")):
                raise PlanError("or-mid requires or_high and or_low")
            if stop == "or-low" and "or_low" not in result:
                raise PlanError("or-low requires or_low")
        else:
            match = PRICE_STOP_RE.match(stop)
            if not match:
                raise PlanError("unsupported stop form")
            stop_value = float(match.group(1))
            if not math.isfinite(stop_value) or stop_value <= 0:
                raise PlanError("stop value must be positive and finite")
            result["stop"] = "%s:%s" % (stop.split(":", 1)[0].lower(), match.group(1))
        if "target" in arguments:
            target = arguments["target"]
            if not isinstance(target, str) or not TARGET_RE.match(target):
                raise PlanError("unsupported target form")
            result["target"] = target
        return result

    if tool == "timemachine" or tool == "alerts_clock" or tool == "budget_size" or tool == "risk_check":
        _check_fields(arguments, ())
        return {}

    if tool == "alerts_quote":
        _check_fields(arguments, ("symbols",), ("symbols",))
        return {"symbols": _symbols(arguments["symbols"], request, maximum=12)}

    if tool == "analyst":
        _check_fields(arguments, ("symbols", "quarters"), ("symbols",))
        result = {"symbols": _symbols(arguments["symbols"], request, maximum=8)}
        if "quarters" in arguments:
            value = arguments["quarters"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 20:
                raise PlanError("quarters must be an integer from 1 through 20")
            result["quarters"] = value
        return result

    raise PlanError("unsupported tool")


def validate_plan(document, request):
    if isinstance(document, str):
        text = document.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
        try:
            document = json.loads(text)
        except (ValueError, TypeError):
            raise PlanError("provider did not return a valid JSON plan")
    if not isinstance(document, dict) or set(document) != {
        "version", "intent", "actions", "clarification"
    }:
        raise PlanError("plan must contain exactly version, intent, actions, and clarification")
    if isinstance(document["version"], bool) or document["version"] != 1:
        raise PlanError("unsupported plan version")
    intent = document["intent"]
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 200:
        raise PlanError("plan intent must be a short string")
    clarification = document["clarification"]
    if clarification is not None and (
        not isinstance(clarification, str) or not clarification.strip() or len(clarification) > 1000
    ):
        raise PlanError("clarification must be null or a short question")
    actions = document["actions"]
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise PlanError("plan must contain at most %d actions" % MAX_ACTIONS)
    if clarification is not None:
        if actions:
            raise PlanError("a clarification plan cannot also run tools")
        return {
            "version": 1,
            "intent": intent.strip(),
            "actions": [],
            "clarification": clarification.strip(),
        }
    if not actions:
        raise PlanError("a plan without actions must ask for clarification")

    normalized = []
    seen = set()
    for index, action in enumerate(actions):
        expected_id = "a%d" % (index + 1)
        if not isinstance(action, dict) or set(action) != {
            "id", "tool", "arguments", "depends_on", "expected_output"
        }:
            raise PlanError("each action must match the version 1 action schema")
        if action["id"] != expected_id:
            raise PlanError("action ids must be sequential a1 through a%d" % len(actions))
        tool = action["tool"]
        if not isinstance(tool, str) or tool not in TOOL_CATALOG:
            raise PlanError("unsupported tool name")
        dependencies = action["depends_on"]
        if not isinstance(dependencies, list) or len(dependencies) > index:
            raise PlanError("depends_on must reference only earlier action ids")
        checked_dependencies = []
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency not in seen:
                raise PlanError("dependency must reference an earlier action")
            if dependency in checked_dependencies:
                raise PlanError("duplicate action dependency")
            checked_dependencies.append(dependency)
        expected = TOOL_CATALOG[tool]["output"]
        if action["expected_output"] != expected:
            raise PlanError("incorrect expected output type for %s" % tool)
        args = _validate_action_arguments(tool, action["arguments"], request)
        required_tools = []
        if tool == "exit_plan" and not args:
            required_tools.append("entry_check")
        if tool in ("timemachine", "budget_size"):
            required_tools.append("exit_plan")
        if tool == "budget_size":
            required_tools.append("alerts_quote")
        if tool == "risk_check":
            required_tools.append("budget_size")
        for required_tool in required_tools:
            prior = next(
                (item for item in reversed(normalized) if item["tool"] == required_tool),
                None,
            )
            if prior is not None and prior["id"] not in checked_dependencies:
                raise PlanError(
                    "%s must depend on prior %s action %s"
                    % (tool, required_tool, prior["id"])
                )
        normalized.append(
            {
                "id": expected_id,
                "tool": tool,
                "arguments": args,
                "depends_on": checked_dependencies,
                "expected_output": expected,
            }
        )
        seen.add(expected_id)
    return {
        "version": 1,
        "intent": intent.strip(),
        "actions": normalized,
        "clarification": None,
    }


def planner_messages(request):
    catalog = []
    for name, spec in TOOL_CATALOG.items():
        catalog.append(
            "- %s: %s Arguments: %s Output: %s"
            % (name, spec["description"], spec["arguments"], spec["output"])
        )
    system = (
        "You are the GE Nexus request planner. Return exactly one JSON object and no "
        "markdown. Do not calculate market facts, invent symbols, data, prices, fills, "
        "or results. Use only uppercase ticker symbols explicitly present in the user "
        "request. If a company name is supplied without its exact ticker, ask for the "
        "ticker in clarification rather than guessing. Ask a clarification when a tool "
        "requires missing inputs or saved prerequisites. Do not propose broker actions, "
        "order placement, fills, arbitrary commands, code, file paths, URLs, or network "
        "requests. The local coordinator validates all actions and arguments.\n\n"
        "Plan format: {\"version\":1,\"intent\":\"short label\",\"actions\":["
        "{\"id\":\"a1\",\"tool\":\"one listed tool\",\"arguments\":{},"
        "\"depends_on\":[],\"expected_output\":\"json or text\"}],"
        "\"clarification\":null}. Use sequential ids a1 to a4. Keep actions to at most "
        "four. If no tool can run yet, use actions:[] and a concise clarification string. "
        "Dependencies can name only earlier action ids. If an exit plan uses a prior "
        "entry check, depend on it; time machine or sizing actions must depend on a prior "
        "exit plan; a risk check must depend on prior sizing; sizing must depend on prior "
        "quotes when included. Do not assume tool outputs are "
        "persisted in the project; intermediate files are private temporary files.\n\n"
        "Allowed tools:\n" + "\n".join(catalog)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": redact_sensitive(request)},
    ]


def _build_command(action):
    tool = action["tool"]
    args = action["arguments"]
    script = str(ROOT / SCRIPT_PATHS[tool])
    command = [sys.executable, script]

    if tool == "scanner_daily":
        command += ["daily", "--symbols", ",".join(args["symbols"]), "--json"]
        if "min_price" in args:
            command += ["--min-price", str(args["min_price"])]
    elif tool == "scanner_orb":
        command += ["orb", "--symbols", ",".join(args["symbols"]), "--json"]
        if "at" in args:
            command += ["--at", args["at"]]
        if "or_minutes" in args:
            command += ["--or-minutes", str(args["or_minutes"])]
    elif tool == "news":
        command += ["--no-sec", "--dry-run", "--json"]
        if "symbols" in args:
            command += ["--tickers"] + args["symbols"]
        if "hours" in args:
            command += ["--hours", str(args["hours"])]
    elif tool == "entry_check":
        command += [
            "check", args["symbol"], "--setup", args["setup"],
            "--interval", args["interval"], "--write", "--json",
        ]
        if "level" in args:
            command += ["--level", str(args["level"])]
        if "min_volume_x" in args:
            command += ["--min-volume-x", str(args["min_volume_x"])]
    elif tool == "exit_plan":
        command += ["plan", "--write", "--json"]
        for field, flag in (
            ("symbol", "--symbol"),
            ("entry", "--entry"),
            ("setup", "--setup"),
            ("or_high", "--or-high"),
            ("or_low", "--or-low"),
            ("stop", "--stop"),
            ("target", "--target"),
        ):
            if field in args:
                command += [flag, str(args[field])]
    elif tool == "timemachine":
        command += ["run", "--write", "--json"]
    elif tool == "alerts_clock":
        command += ["clock", "--json"]
    elif tool == "alerts_quote":
        command += ["quote"] + args["symbols"] + ["--write"]
    elif tool == "analyst":
        command += args["symbols"] + ["--no-filings", "--json"]
        if "quarters" in args:
            command += ["--quarters", str(args["quarters"])]
    elif tool == "budget_size":
        command += ["--write", "--json"]
    elif tool == "risk_check":
        command += ["--no-write", "--json"]
    else:
        raise PlanError("tool has no local command mapping")
    return command


def _copy_runtime_inputs(workspace):
    source = ROOT / "trader"
    warnings = []
    if not source.is_dir():
        return warnings
    destination = workspace / "trader"
    destination.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_INPUTS:
        _copy_regular_file(source / name, destination / name, warnings)
    state_source = source / "state"
    if state_source.is_dir():
        state_destination = destination / "state"
        state_destination.mkdir(parents=True, exist_ok=True)
        for name in RUNTIME_STATE_INPUTS:
            _copy_regular_file(state_source / name, state_destination / name, warnings)
    return warnings


def _copy_regular_file(source, destination, warnings):
    try:
        info = source.lstat()
    except OSError:
        return
    if source.is_symlink() or not source.is_file():
        warnings.append("Ignored non-regular runtime input: %s" % source.name)
        return
    if info.st_size > RUNTIME_FILE_LIMIT:
        warnings.append("Ignored oversized runtime input: %s" % source.name)
        return
    try:
        shutil.copyfile(str(source), str(destination))
    except OSError:
        warnings.append("Could not copy runtime input: %s" % source.name)


def _child_environment():
    env = os.environ.copy()
    for name in API_KEY_ENV_NAMES:
        env.pop(name, None)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _read_bounded_pipe(fd, name, buffers, capture_state):
    try:
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            with capture_state["lock"]:
                remaining = MAX_TOOL_OUTPUT_BYTES - capture_state["size"]
                kept = chunk[:remaining] if remaining > 0 else b""
                if kept:
                    buffers[name].extend(kept)
                    capture_state["size"] += len(kept)
                if len(kept) != len(chunk):
                    capture_state["overflow"] = True
    finally:
        os.close(fd)


def _run_bounded_subprocess(command, workspace, environment, timeout):
    """Use subprocess.run while draining stdout/stderr into a fixed-size buffer."""
    stdout_read, stdout_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    capture_state = {"lock": threading.Lock(), "size": 0, "overflow": False}
    readers = [
        threading.Thread(
            target=_read_bounded_pipe,
            args=(stdout_read, "stdout", buffers, capture_state),
            daemon=True,
        ),
        threading.Thread(
            target=_read_bounded_pipe,
            args=(stderr_read, "stderr", buffers, capture_state),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    completed = None
    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace),
            env=environment,
            shell=False,
            stdout=stdout_write,
            stderr=stderr_write,
            timeout=timeout,
            check=False,
        )
    finally:
        os.close(stdout_write)
        os.close(stderr_write)
        for reader in readers:
            reader.join()
    return (
        completed,
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
        capture_state["overflow"],
    )


def _run_one(action, workspace, deadline):
    command = _build_command(action)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {
            "id": action["id"], "tool": action["tool"], "status": "skipped",
            "category": "request_deadline_exceeded",
        }
    timeout = min(TOOL_TIMEOUT_SECONDS, remaining)
    try:
        completed, stdout_bytes, stderr_bytes, overflow = _run_bounded_subprocess(
            command,
            workspace,
            _child_environment(),
            timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "id": action["id"], "tool": action["tool"], "status": "failed",
            "category": "tool_timeout",
        }
    except OSError:
        return {
            "id": action["id"], "tool": action["tool"], "status": "failed",
            "category": "tool_start_failed",
        }

    # Keep test doubles and wrappers returning CompletedProcess output compatible;
    # the real invocation streams directly into the bounded pipes above.
    if completed.stdout is not None:
        stdout_bytes = (
            completed.stdout.encode("utf-8", "replace")
            if isinstance(completed.stdout, str)
            else completed.stdout
        )
    if completed.stderr is not None:
        stderr_bytes = (
            completed.stderr.encode("utf-8", "replace")
            if isinstance(completed.stderr, str)
            else completed.stderr
        )
    stdout = stdout_bytes.decode("utf-8", "replace")
    stderr = redact_sensitive(stderr_bytes.decode("utf-8", "replace"))
    if overflow or len(stdout_bytes) + len(stderr_bytes) > MAX_TOOL_OUTPUT_BYTES:
        return {
            "id": action["id"], "tool": action["tool"], "status": "failed",
            "category": "tool_output_too_large",
            "returncode": completed.returncode,
        }
    result = {
        "id": action["id"],
        "tool": action["tool"],
        "status": "failed",
        "returncode": completed.returncode,
    }
    if stderr:
        result["stderr_preview"] = stderr[:2000]
    if completed.returncode != 0:
        result["category"] = "tool_exit_nonzero"
        result["stdout_preview"] = redact_sensitive(stdout)[:2000]
        return result

    if action["expected_output"] == "json":
        try:
            data = json.loads(stdout)
        except (ValueError, TypeError):
            result["category"] = "invalid_json_output"
            result["stdout_preview"] = redact_sensitive(stdout)[:2000]
            return result
        if not isinstance(data, dict) or not data:
            result["category"] = "invalid_json_output"
            result["stdout_preview"] = redact_sensitive(stdout)[:2000]
            return result
        result["data"] = json.loads(
            redact_sensitive(json.dumps(data, ensure_ascii=False))
        )
    else:
        result["data"] = redact_sensitive(stdout)
        if not result["data"].strip():
            result["category"] = "empty_output"
            return result
    result["status"] = "completed"
    result["dependency_ready"] = _dependency_ready(action["tool"], result["data"])
    return result


def _dependency_ready(tool, data):
    if tool == "entry_check" and isinstance(data, dict):
        status = data.get("status")
        return status == "triggered"
    return True


def _run_actions(plan, deadline):
    results = []
    by_id = {}
    with tempfile.TemporaryDirectory(prefix="ge-nexus-ai-") as temporary:
        workspace = Path(temporary)
        warnings = _copy_runtime_inputs(workspace)
        for action in plan["actions"]:
            failed_dependencies = [
                dependency
                for dependency in action["depends_on"]
                if by_id.get(dependency, {}).get("status") != "completed"
                or not by_id.get(dependency, {}).get("dependency_ready", False)
            ]
            if failed_dependencies:
                result = {
                    "id": action["id"],
                    "tool": action["tool"],
                    "status": "skipped",
                    "category": "prerequisite_failed",
                    "depends_on": failed_dependencies,
                }
            else:
                result = _run_one(action, workspace, deadline)
            results.append(result)
            by_id[action["id"]] = result
    return results, warnings


def _action_output_for_model(results):
    bounded = []
    for result in results:
        item = {
            "id": result["id"],
            "tool": result["tool"],
            "status": result["status"],
        }
        if "category" in result:
            item["category"] = result["category"]
        if "returncode" in result:
            item["returncode"] = result["returncode"]
        if "data" in result:
            serialized = redact_sensitive(
                json.dumps(result["data"], ensure_ascii=False, separators=(",", ":"))
            )
            truncated = len(serialized) > MAX_MODEL_ACTION_OUTPUT_CHARS
            item["data_preview"] = serialized[:MAX_MODEL_ACTION_OUTPUT_CHARS]
            item["truncated"] = truncated
        bounded.append(item)
    return bounded


def _parse_interpretation(content):
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("interpretation was not valid JSON")
    if not isinstance(data, dict) or set(data) != {
        "summary", "reported_facts", "interpretation", "limitations"
    }:
        raise ValueError("interpretation did not match the required schema")
    if not isinstance(data["summary"], str) or len(data["summary"]) > 2000:
        raise ValueError("invalid summary")
    if not isinstance(data["interpretation"], str) or len(data["interpretation"]) > 5000:
        raise ValueError("invalid interpretation")
    for field in ("reported_facts", "limitations"):
        values = data[field]
        if not isinstance(values, list) or len(values) > 20:
            raise ValueError("invalid %s" % field)
        if any(not isinstance(value, str) or len(value) > 1000 for value in values):
            raise ValueError("invalid %s item" % field)
    return data


def _interpret(request, plan, results, deadline):
    evidence = {
        "intent": plan["intent"],
        "actions": _action_output_for_model(results),
    }
    system = (
        "You are GE Nexus's result explainer. Explain only what the supplied Python "
        "results support. Never invent or infer prices, timestamps, indicators, news, "
        "risk values, data freshness, fills, or order execution. Distinguish facts "
        "reported by Python from your interpretation. State failures, missing data, "
        "and truncation plainly. A setup is not a guarantee or a recommendation. "
        "Return only JSON with exactly these fields: {\"summary\":\"...\","
        "\"reported_facts\":[\"...\"],\"interpretation\":\"...\","
        "\"limitations\":[\"...\"]}. Do not include commands or claims of broker "
        "activity."
    )
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                "User request: " + redact_sensitive(request) + "\n"
                "Python evidence (data_preview values are serialized tool output; "
                "they may be truncated):\n" + json.dumps(evidence, ensure_ascii=False)
            ),
        },
    ]
    try:
        response = provider.chat_completion(
            messages, deadline=deadline, validate_content=_parse_interpretation
        )
    except provider.ProviderUnavailable as exc:
        return None, exc.as_dict(), None
    parsed = response.get("validated")
    if parsed is None:
        try:
            parsed = _parse_interpretation(response["content"])
        except ValueError:
            return None, {
                "category": "invalid_interpretation",
                "message": "The selected provider returned an invalid interpretation. Python results are preserved below.",
            }, response["provider"]
    return parsed, None, response["provider"]


def coordinate(request):
    if not isinstance(request, str) or not request.strip():
        return {
            "status": "invalid_request",
            "error": {"category": "empty_request", "message": "Enter a market-analysis request."},
        }
    request = request.strip()
    if len(request) > MAX_REQUEST_CHARS:
        return {
            "status": "invalid_request",
            "error": {
                "category": "request_too_long",
                "message": "Keep the request to %d characters or fewer." % MAX_REQUEST_CHARS,
            },
        }

    safe_request = redact_sensitive(request)
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    try:
        planned = provider.chat_completion(
            planner_messages(request),
            deadline=deadline,
            validate_content=lambda content: validate_plan(content, request),
        )
    except provider.ProviderUnavailable as exc:
        return {
            "status": "unavailable",
            "request": safe_request,
            "error": exc.as_dict(),
            "actions": [],
            "interpretation": None,
        }
    try:
        plan = planned.get("validated")
        if plan is None:
            plan = validate_plan(planned["content"], request)
    except PlanError as exc:
        return {
            "status": "plan_rejected",
            "request": safe_request,
            "provider": planned["provider"],
            "error": {
                "category": "invalid_plan",
                "message": str(exc),
            },
            "actions": [],
            "interpretation": None,
        }

    base = {
        "request": safe_request,
        "intent": plan["intent"],
        "plan": plan,
        "planning_provider": planned["provider"],
    }
    if plan["clarification"] is not None:
        base["status"] = "clarification"
        base["clarification"] = plan["clarification"]
        base["actions"] = []
        base["interpretation"] = None
        return base

    action_results, warnings = _run_actions(plan, deadline)
    base["actions"] = action_results
    if warnings:
        base["runtime_input_warnings"] = warnings
    interpretation, error, interpreter = _interpret(request, plan, action_results, deadline)
    base["interpretation_provider"] = interpreter
    base["interpretation"] = interpretation
    if error:
        base["interpretation_error"] = error
    if all(item["status"] == "completed" for item in action_results):
        base["status"] = "complete" if interpretation else "results_available"
    else:
        base["status"] = "partial"
    return base


def render_result(result):
    lines = ["GE Nexus AI Coordinator"]
    if result.get("status") == "clarification":
        lines.append("Need more information: " + result["clarification"])
        return "\n".join(lines)
    if "error" in result:
        lines.append(result["error"].get("message", "The request could not be completed."))
        attempts = result["error"].get("attempts", [])
        if attempts:
            lines.append(
                "Provider attempts: "
                + ", ".join("%s (%s)" % (x["provider"], x["category"]) for x in attempts)
            )
        return "\n".join(lines)

    lines.append("Intent: " + result.get("intent", "analysis"))
    if result.get("interpretation"):
        interpretation = result["interpretation"]
        lines.append("\nSummary: " + interpretation["summary"])
        if interpretation["reported_facts"]:
            lines.append("\nReported by Python:")
            lines.extend("- " + fact for fact in interpretation["reported_facts"])
        if interpretation["interpretation"]:
            lines.append("\nAI interpretation:")
            lines.append(interpretation["interpretation"])
        if interpretation["limitations"]:
            lines.append("\nLimitations:")
            lines.extend("- " + item for item in interpretation["limitations"])
    else:
        lines.append("\nAI interpretation unavailable; showing Python results directly.")
        if result.get("interpretation_error"):
            lines.append(result["interpretation_error"]["message"])

    lines.append("\nPython tool results:")
    for action in result.get("actions", []):
        lines.append("- %s (%s): %s" % (action["id"], action["tool"], action["status"]))
        if action.get("category"):
            lines.append("  Reason: " + action["category"])
        if "data" in action:
            value = action["data"]
            rendered = json.dumps(value, ensure_ascii=False, indent=2)
            if len(rendered) > 12000:
                rendered = rendered[:12000] + "\n[display truncated; complete result available with --json]"
            lines.append(rendered)
    if result.get("runtime_input_warnings"):
        lines.append("\nRuntime input notes:")
        lines.extend("- " + warning for warning in result["runtime_input_warnings"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plan and run a restricted set of existing GE Nexus analysis tools."
    )
    parser.add_argument(
        "--json", action="store_true", help="print the structured result as JSON"
    )
    parser.add_argument(
        "request",
        nargs="+",
        help="natural-language analysis request (ticker symbols must be explicit, e.g. TSLA)",
    )
    args = parser.parse_args(argv)
    result = coordinate(" ".join(args.request))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_result(result))
    return 0 if result.get("status") in ("complete", "results_available", "clarification") else 1


if __name__ == "__main__":
    sys.exit(main())
