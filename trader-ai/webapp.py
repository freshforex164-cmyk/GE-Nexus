"""GE Nexus web API and PWA shell.

The browser can request only the named analysis endpoints below. It never
chooses scripts, arguments, providers, or filesystem paths.
"""

import json
import hmac
import math
import os
import re
import secrets
import sys
import threading
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone
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
MAX_SYMBOLS = 20
MAX_SYMBOL_LENGTH = 30
DEFAULT_SCAN_SYMBOLS = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA")
MAX_REQUESTS_PER_WINDOW = 5
ANALYSIS_WINDOW_SECONDS = 15 * 60
MAX_SCANS_PER_WINDOW = 12
SCAN_WINDOW_SECONDS = 60
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$")
ACCESS_PASSWORD_ENV = "GE_NEXUS_ACCESS_PASSWORD"
MAX_RESEARCH_CONTEXTS = 80
RESEARCH_CONTEXT_TTL = 60 * 60
_research_contexts = OrderedDict()
_research_contexts_lock = threading.Lock()
_provider_checks = {}
_provider_checks_lock = threading.Lock()


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


#@app.before_request
def require_deployment_password():
    password = os.environ.get(ACCESS_PASSWORD_ENV, "").strip()
    if not password or request.path == "/api/health":
        return None

    #authorization = request.authorization
    #if (
       # authorization is not None
        #and authorization.type.lower() == "basic"
       # and hmac.compare_digest(authorization.username or "", "ge-nexus")
      #  and hmac.compare_digest(authorization.password or "", password)
   # ):
       # return None

    #response = app.response_class(
       # "GE Nexus requires HTTP Basic authentication.",
       # status=401,
       #l mimetype="text/plain",
   # )
   # response.headers["WWW-Authenticate"] = 'Basic realm="GE Nexus", charset="UTF-8"'
   # return response


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
        model, model_source = coordinator.provider.selected_model(spec)
        has_model = bool(model)
        endpoint = os.environ.get(spec.endpoint_env) or spec.endpoint
        try:
            coordinator.provider._validate_endpoint(endpoint)
            endpoint_valid = True
        except ValueError:
            endpoint_valid = False
        configured = has_key and has_model and endpoint_valid
        with _provider_checks_lock:
            last_check = _provider_checks.get(spec.name)
        connection_verified = bool(
            last_check and last_check.get("status") == "verified"
        )
        providers.append(
            {
                "name": spec.name,
                "priority": priority,
                "key_env": spec.key_env,
                "model_env": spec.model_env,
                "endpoint_env": spec.endpoint_env,
                "key_configured": has_key,
                "model_configured": has_model,
                "selected_model": model,
                "model_source": model_source,
                "provider_available": connection_verified,
                "configured": configured,
                "connection_verified": connection_verified,
                "last_connection_result": last_check,
                "state": (
                    "misconfigured"
                    if not endpoint_valid
                    else "verified"
                    if connection_verified
                    else "ready_to_test"
                    if configured
                    else "unavailable"
                    if not has_key or not has_model
                    else "misconfigured"
                ),
            }
        )
    return providers


def _remember_provider_check(attempts, verified_provider=None, verified_model=None):
    """Keep only safe, non-secret results from the latest ordered connection test."""
    checked_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    by_provider = {
        item.get("provider"): item.get("category")
        for item in attempts
        if isinstance(item, dict)
    }
    if verified_provider:
        by_provider[verified_provider] = "verified"
    with _provider_checks_lock:
        for spec in coordinator.provider.PROVIDERS:
            category = by_provider.get(spec.name)
            if category is None:
                status = "not_attempted"
            elif category == "verified":
                status = "verified"
            elif category == "missing_key":
                status = "unavailable"
            else:
                status = "failed"
            result = {"status": status, "checked_at": checked_at}
            if category and category != "verified":
                result["category"] = category
            if status == "verified" and verified_model:
                result["model"] = verified_model
            _provider_checks[spec.name] = result


def _has_regular_runtime_file(name):
    path = ROOT / "trader" / name
    try:
        info = path.lstat()
        return (
            not path.is_symlink()
            and path.is_file()
            and info.st_size <= coordinator.RUNTIME_FILE_LIMIT
        )
    except OSError:
        return False


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


def _interpret_action(result, request_text):
    plan = {"intent": request_text}
    evidence_result = dict(result)
    if result.get("scan_summary") or result.get("research_findings"):
        evidence_result["data"] = {
            "scan_summary": result.get("scan_summary"),
            "findings": result.get("research_findings", []),
            "explanation": result.get("research_explanation", []),
        }
    interpretation, error, interpretation_provider = coordinator._interpret(
        request_text,
        plan,
        [evidence_result],
        time.monotonic() + coordinator.MAX_TOTAL_SECONDS,
    )
    result["interpretation"] = interpretation
    result["interpretation_provider"] = interpretation_provider
    if error:
        result["interpretation_error"] = error
    result["ai_status"] = "complete" if interpretation else "unavailable"
    return result


def _validate_provider_check_response(content):
    if not isinstance(content, str) or content.strip().casefold() != "ok":
        raise ValueError("provider did not return the expected health-check response")
    return "OK"


_SETUP_EXPLANATIONS = {
    "breakout_20d": (
        "The latest completed daily close was above the preceding 20 daily closes."
    ),
    "bull_flag": (
        "The prior 10-session move was over 10%, followed by five closes in a range under 5%."
    ),
    "reclaim_ma20": (
        "The latest close moved above its 20-day average after the previous close was below its prior average."
    ),
    "oversold": "The scanner's 14-day Wilder RSI was below 30.",
    "oversold_bounce": (
        "The prior day's 14-day Wilder RSI was below 30 and the latest close was higher."
    ),
    "orb": (
        "A completed five-minute bar closed above the high of the opening range."
    ),
}
_SETUP_LABELS = {
    "breakout_20d": "20-day breakout",
    "bull_flag": "Bull flag pattern",
    "reclaim_ma20": "20-day average reclaim",
    "oversold": "RSI below 30",
    "oversold_bounce": "Oversold bounce",
    "orb": "Opening-range breakout",
}


def _scan_summary(
    result, valid_symbols, mode, invalid_symbols=(), duplicate_symbols=()
):
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    hits = data.get("hits") if isinstance(data.get("hits"), list) else []
    analyzed = int(data.get("scanned") or counts.get("scanned") or 0)
    symbol_results = data.get("symbol_results")
    if isinstance(symbol_results, list):
        analyzed = sum(
            1 for item in symbol_results
            if isinstance(item, dict)
            and item.get("status") in ("completed", "no_qualifying_result")
        )
    errors = max(0, int(data.get("failed") or 0))
    unconfirmed = max(0, int(data.get("unconfirmed") or counts.get("unconfirmed") or 0))
    failed_symbols = data.get("failed_symbols")
    if not isinstance(failed_symbols, list):
        failed_symbols = []
    failed_symbols = [
        symbol for symbol in failed_symbols
        if isinstance(symbol, str) and TICKER_RE.fullmatch(symbol)
    ][:MAX_SYMBOLS]
    if isinstance(symbol_results, list):
        detailed_failures = [
            item.get("symbol") for item in symbol_results
            if isinstance(item, dict)
            and item.get("status") in (
                "no_market_data", "unsupported_symbol", "temporarily_unavailable",
                "analysis_error",
            )
            and isinstance(item.get("symbol"), str)
            and TICKER_RE.fullmatch(item["symbol"])
        ]
        failed_symbols = list(dict.fromkeys(failed_symbols + detailed_failures))[:MAX_SYMBOLS]
        errors = max(errors, len(detailed_failures))
    partial_symbols = sum(
        1 for item in symbol_results or []
        if isinstance(item, dict) and item.get("status") == "analysis_incomplete"
    )
    requested = len(valid_symbols) + len(invalid_symbols)
    failures = errors + len(invalid_symbols)
    if result.get("status") != "completed":
        state = "failed"
        label = "Scan failed"
    elif analyzed == 0:
        state = "data_unavailable"
        label = "No usable market data"
    elif errors or partial_symbols or invalid_symbols or unconfirmed:
        state = "partial"
        label = "Completed with some symbols unavailable"
    elif not hits:
        state = "no_findings"
        label = "No qualifying findings"
    else:
        state = "completed"
        label = "Completed"
    return {
        "status": state,
        "label": label,
        "completed_at": data.get("created_at"),
        "instruments_checked": requested,
        "instruments_analyzed": analyzed,
        "instruments_partial": partial_symbols,
        "instruments_failed": failures,
        "candidate_count": len(hits),
        "data_errors": errors,
        "data_error_symbols": failed_symbols,
        "invalid_symbols": list(invalid_symbols),
        "duplicates_removed": list(duplicate_symbols),
        "unconfirmed_candidates": unconfirmed,
        "data_source": (
            "Yahoo Finance chart data (unofficial; may be delayed)"
            if data.get("source") == "yahoo"
            else data.get("source") or "Not reported by the scanner"
        ),
        "data_as_of": data.get("as_of"),
        "mode": mode,
        "note": (
            "The scanner returns rule-matched research observations, not trade recommendations."
        ),
    }


def _research_findings(data):
    hits = data.get("hits") if isinstance(data.get("hits"), list) else []
    mode = data.get("mode")
    findings = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        symbol = hit.get("symbol")
        if not isinstance(symbol, str) or not TICKER_RE.fullmatch(symbol):
            continue
        setup_keys = hit.get("setups") if isinstance(hit.get("setups"), list) else []
        findings.append(
            {
                "symbol": symbol,
                "setups": [
                    {
                        "key": key,
                        "label": _SETUP_LABELS[key],
                        "explanation": _SETUP_EXPLANATIONS[key],
                    }
                    for key in setup_keys
                    if isinstance(key, str) and key in _SETUP_EXPLANATIONS
                ],
                "price": hit.get("price"),
                "price_as_of": hit.get("close_date") or data.get("as_of"),
                "price_source": (
                    "Yahoo Finance chart data (unofficial; may be delayed)"
                    if data.get("source") == "yahoo"
                    else data.get("source")
                ),
                "rsi_14": hit.get("rsi"),
                "volume": hit.get("volume"),
                "bar_time": hit.get("at"),
                "opening_range_low": hit.get("or_low"),
                "opening_range_high": hit.get("or_high"),
                "percent_above_range": hit.get("pct_over"),
            }
        )
    return findings


def _structure_scan_result(
    result, requested_symbols, valid_symbols, mode, invalid_symbols=(), duplicate_symbols=()
):
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    invalid_symbols = list(invalid_symbols)
    duplicate_symbols = list(duplicate_symbols)
    invalid_set = set(invalid_symbols)
    summary = _scan_summary(
        result, valid_symbols, mode, invalid_symbols, duplicate_symbols
    )
    findings = _research_findings(data)
    raw_results = data.get("symbol_results")
    if not isinstance(raw_results, list):
        raw_results = []
    by_symbol = {
        item.get("symbol"): item
        for item in raw_results
        if isinstance(item, dict)
        and isinstance(item.get("symbol"), str)
        and TICKER_RE.fullmatch(item["symbol"])
    }
    findings_by_symbol = {item["symbol"]: item for item in findings}
    normalized_results = []
    failed_symbols = set(summary["data_error_symbols"])
    for symbol in requested_symbols:
        if symbol in invalid_set:
            normalized_results.append(
                {
                    "symbol": symbol,
                    "analysis_status": "invalid_symbol",
                    "assessment": "INSUFFICIENT DATA",
                    "assessment_note": "This entry is not a supported ticker format.",
                    "instrument_name": None,
                    "data_source": None,
                    "data_timestamp": None,
                    "latest_price": None,
                    "market_price": None,
                    "timeframe": None,
                    "market_bias": None,
                    "scanner_findings": [],
                    "technical_indicators": {},
                    "entry_price": None,
                    "stop_loss": None,
                    "take_profit": None,
                    "invalidation": None,
                    "historical_simulation": None,
                    "risk": None,
                    "data_quality": "insufficient",
                    "tool_execution_status": "not_run",
                    "python_signal": None,
                    "reasons": ["Invalid ticker symbol; other valid symbols were still scanned."],
                    "warnings": ["Invalid ticker symbol."],
                    "tool_result": None,
                }
            )
            continue
        raw = by_symbol.get(symbol, {})
        finding = findings_by_symbol.get(symbol)
        status = raw.get("status")
        if status not in {
            "completed", "no_qualifying_result", "no_market_data",
            "unsupported_symbol", "temporarily_unavailable",
            "analysis_error", "analysis_incomplete",
        }:
            if result.get("status") != "completed":
                status = "analysis_error"
            elif finding:
                status = "completed"
            elif symbol in failed_symbols:
                status = "no_market_data"
            elif summary["instruments_analyzed"] > 0:
                status = "no_qualifying_result"
            else:
                status = "no_market_data"
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = (
                "The existing scanner returned a qualifying pattern."
                if finding
                else "The existing scanner returned no qualifying pattern."
                if status == "no_qualifying_result"
                else "The existing scanner did not return usable data for this symbol."
            )
        directional = "WAIT" if status == "no_qualifying_result" else (
            "NO RESULT" if status in {
                "no_market_data", "unsupported_symbol", "temporarily_unavailable",
                "analysis_error", "analysis_incomplete",
            } else None
        )
        normalized_results.append(
            {
                "symbol": symbol,
                "instrument_name": raw.get("instrument_name"),
                "data_source": data.get("source"),
                "data_timestamp": raw.get("price_as_of") or data.get("as_of"),
                "latest_price": raw.get("price", finding.get("price") if finding else None),
                "market_bias": None,
                "scanner_findings": finding.get("setups", []) if finding else [],
                "technical_indicators": (
                    {"rsi_14": finding.get("rsi_14")}
                    if finding and finding.get("rsi_14") is not None else {}
                ),
                "invalidation": None,
                "historical_simulation": None,
                "data_quality": (
                    "complete" if status in ("completed", "no_qualifying_result")
                    else "partial" if status == "analysis_incomplete"
                    else "insufficient"
                ),
                "tool_execution_status": status,
                "python_signal": (
                    raw.get("signal")
                    if isinstance(raw.get("signal"), str)
                    and raw.get("signal").upper()
                    in ("BUY", "SELL", "WAIT", "INSUFFICIENT DATA")
                    else None
                ),
                "analysis_status": status,
                "assessment": directional,
                "assessment_note": (
                    "A scanner finding is not a BUY or SELL signal."
                    if finding else None
                ),
                "market_price": raw.get("price", finding.get("price") if finding else None),
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "risk": None,
                "position_size": None,
                "timeframe": raw.get("timeframe") or data.get("interval") or (
                    "1d" if mode == "daily" else "5m"
                ),
                "analysis_timestamp": data.get("created_at"),
                "price_as_of": raw.get("price_as_of") or (
                    finding.get("price_as_of") if finding else data.get("as_of")
                ),
                "reasons": [reason],
                "warnings": (
                    [reason] if status in {
                        "no_market_data", "unsupported_symbol", "temporarily_unavailable",
                        "analysis_error", "analysis_incomplete",
                    } else []
                ),
                "tool_result": finding,
            }
        )
    explanation = [
        (
            "Daily mode checks completed daily closes for 20-day breakouts, bull-flag patterns, "
            "20-day-average reclaims, and RSI-14 oversold conditions. The current session's daily "
            "bar is excluded until it closes."
        )
        if mode == "daily"
        else (
            "Opening-range mode checks completed five-minute bars against the first 15 minutes "
            "of the regular session. It runs only when the market clock confirms a usable session."
        ),
        "Yahoo Finance data is unofficial and may be delayed or unavailable.",
        "The scanner does not calculate an entry, stop, target, or risk/reward for these findings.",
    ]
    result["scan_summary"] = summary
    result["research_findings"] = findings
    result["symbol_results"] = normalized_results
    result["research_explanation"] = explanation
    result["research_output"] = {
        "status": result.get("status"),
        "data_source": data.get("source"),
        "timestamp": data.get("created_at"),
        "as_of": data.get("as_of"),
        "timeframe": data.get("interval") or ("1d" if mode == "daily" else "5m"),
        "signal_classification": None,
        "findings": findings,
        "symbols": normalized_results,
        "warnings": explanation[1:],
        "data_quality": summary["status"],
    }
    return result


def _configured_orb_cutoff():
    """Read only the bounded, regular runtime config used by the scanner."""
    path = ROOT / "trader" / "config.json"
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_size > coordinator.RUNTIME_FILE_LIMIT:
            return "15:30"
        document = json.loads(path.read_text(encoding="utf-8"))
        value = document.get("no_entries_after") if isinstance(document, dict) else None
        if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}", value):
            datetime.strptime(value, "%H:%M")
            return value
    except (OSError, UnicodeError, ValueError, TypeError):
        pass
    return "15:30"


def _automatic_scan_mode():
    """Choose intraday only when the existing exchange clock confirms a valid window."""
    clock, _warnings = _execute_validated_action(
        "alerts_clock", {}, "Check the exchange clock before choosing a scanner"
    )
    clock_data = clock.get("data") if isinstance(clock.get("data"), dict) else {}
    if (
        clock.get("status") != "completed"
        or not clock_data.get("calendar_known")
        or not clock_data.get("open")
    ):
        return "daily", "The exchange clock did not confirm a usable intraday session."
    try:
        now = datetime.strptime(clock_data["now_new_york"], "%Y-%m-%d %H:%M")
        opens = datetime.strptime(clock_data["opens"], "%H:%M").time()
        closes = datetime.strptime(clock_data["closes"], "%H:%M").time()
        last_entry = datetime.strptime(_configured_orb_cutoff(), "%H:%M").time()
        opening_range_end = datetime.combine(now.date(), opens).replace(
            minute=opens.minute
        )
        opening_range_end += __import__("datetime").timedelta(minutes=20)
        end = min(
            datetime.combine(now.date(), closes),
            datetime.combine(now.date(), last_entry),
        )
        if opening_range_end <= now < end:
            return "orb", "The exchange clock confirms an open session and a usable opening-range window."
    except (KeyError, TypeError, ValueError, OverflowError):
        return "daily", "The exchange clock response was incomplete; a daily-close scan is safer."
    return "daily", "Outside the opening-range scan window, the daily-close scan is used."


def _remember_research(result):
    """Retain bounded Python evidence for an explicitly requested interpretation."""
    if result.get("status") != "completed":
        return None
    evidence = {
        "tool": result.get("tool"),
        "status": result.get("status"),
        "symbol": result.get("symbol"),
        "data": result.get("data"),
        "scan_summary": result.get("scan_summary"),
        "research_findings": result.get("research_findings"),
        "research_explanation": result.get("research_explanation"),
        "research_output": result.get("research_output"),
        "symbol_results": result.get("symbol_results"),
    }
    try:
        if len(json.dumps(evidence, ensure_ascii=False)) > 100000:
            return None
    except (TypeError, ValueError):
        return None
    now = time.monotonic()
    with _research_contexts_lock:
        expired = [
            key for key, value in _research_contexts.items()
            if now - value["created"] > RESEARCH_CONTEXT_TTL
        ]
        for key in expired:
            _research_contexts.pop(key, None)
        research_id = secrets.token_urlsafe(24)
        _research_contexts[research_id] = {"created": now, "evidence": evidence}
        while len(_research_contexts) > MAX_RESEARCH_CONTEXTS:
            _research_contexts.popitem(last=False)
    return research_id


def _python_value(document, keys):
    if not isinstance(document, dict):
        return None
    for key in keys:
        value = document.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return value
    return None


def _research_interpretation_input(evidence, symbol):
    """Build one symbol's bounded evidence and immutable fields for the model."""
    rows = evidence.get("symbol_results")
    rows = rows if isinstance(rows, list) else []
    row = next(
        (
            item for item in rows
            if isinstance(item, dict) and item.get("symbol") == symbol
        ),
        {},
    )
    if not row and evidence.get("symbol") != symbol:
        raise ValueError("The chosen symbol is not part of this completed result.")
    data = evidence.get("data") if isinstance(evidence.get("data"), dict) else {}
    findings = evidence.get("research_findings")
    findings = findings if isinstance(findings, list) else []
    finding = next(
        (item for item in findings if isinstance(item, dict) and item.get("symbol") == symbol),
        None,
    )
    source_signal = row.get("python_signal")
    signal = source_signal if source_signal in ("BUY", "SELL", "WAIT") else "WAIT"
    quality = {
        "complete": "good",
        "partial": "limited",
        "insufficient": "insufficient",
    }.get(row.get("data_quality"), "insufficient")
    if row.get("analysis_status") in (
        "no_market_data", "unsupported_symbol", "temporarily_unavailable",
        "analysis_error", "analysis_incomplete", "invalid_symbol",
    ):
        quality = "insufficient" if row.get("analysis_status") != "analysis_incomplete" else "limited"
    # A Python signal, when present, is authoritative. The scanner currently
    # reports findings rather than directional trade signals; those remain WAIT.
    fields = {
        "symbol": symbol,
        "signal": signal,
        "reason": "",
        "entry_price": _python_value(row, ("entry_price", "entry"))
        if row else _python_value(data, ("entry_price", "entry")),
        "stop_loss": _python_value(row, ("stop_loss", "stop"))
        if row else _python_value(data, ("stop_loss", "stop")),
        "take_profit": _python_value(row, ("take_profit", "target"))
        if row else _python_value(data, ("take_profit", "target")),
        "risk_reward": _python_value(row, ("risk_reward", "reward_to_risk"))
        if row else _python_value(data, ("risk_reward", "reward_to_risk")),
        "confidence": _python_value(row, ("confidence", "confidence_score"))
        if row else _python_value(data, ("confidence", "confidence_score")),
        "data_quality": quality,
        "missing_information": [],
        "warnings": [],
    }
    for key in ("entry_price", "stop_loss", "take_profit", "risk_reward", "confidence"):
        if fields[key] is None:
            fields["missing_information"].append(key + " was not calculated by the Python result.")
    explanations = evidence.get("research_explanation")
    warnings = [
        item for item in (row.get("warnings", []) if row else [])
        if isinstance(item, str)
    ]
    if isinstance(explanations, list):
        warnings.extend(
            item for item in explanations
            if isinstance(item, str) and ("unofficial" in item.lower() or "delayed" in item.lower())
        )
    fields["warnings"] = list(dict.fromkeys(warnings))[:8]
    selected = {
        "status": evidence.get("status"),
        "tool": evidence.get("tool"),
        "symbol": symbol,
        "data": evidence.get("data") if not row else None,
        "symbol_result": row or None,
        "finding": finding,
        "scan_summary": evidence.get("scan_summary"),
    }
    return fields, selected


def _single_symbol(body):
    symbol = body.get("symbol")
    if not isinstance(symbol, str):
        return None, _api_error("invalid_symbol", "Enter one valid ticker symbol.", 400)
    symbol = symbol.strip().upper()
    if not TICKER_RE.fullmatch(symbol):
        return None, _api_error("invalid_symbol", "Enter one valid ticker symbol.", 400)
    return symbol, None


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
    engine_available = all(
        (ROOT / path).is_file()
        for path in set(coordinator.SCRIPT_PATHS.values())
    )
    body = {
        "ok": engine_available,
        "service": "ge-nexus",
        "backend": "healthy",
        "engine": {"available": engine_available},
    }
    return jsonify(body), 200 if engine_available else 503


@app.get("/api/status")
def status():
    engine_available = all(
        (ROOT / path).is_file()
        for path in set(coordinator.SCRIPT_PATHS.values())
    )
    return jsonify(
        {
            "service": "GE Nexus",
            "backend": {"healthy": True},
            "engine": {
                "available": engine_available,
                "interface": "existing Python tools",
            },
            "market_data": {
                "availability": "not_checked",
                "source": "Yahoo Finance chart data (unofficial; may be delayed)",
            },
            "providers": _provider_status(),
            "provider_checks_are_live": False,
            "risk_inputs_available": (
                _has_regular_runtime_file("orders.json")
                and _has_regular_runtime_file("config.json")
            ),
            "watchlist": _watchlist_summary(),
            "analysis_history": {
                "available": True,
                "persistent": "browser_local_storage",
                "server_database": False,
            },
            "capabilities": {
                "natural_language_coordination": False,
                "result_interpretation": True,
                "market_scanner": True,
                "broker_execution": False,
            },
        }
    )


@app.get("/api/universe")
def scan_universe():
    return jsonify(
        {
            "default_symbols": list(DEFAULT_SCAN_SYMBOLS),
            "max_symbols": MAX_SYMBOLS,
            "market": {
                "supported": [
                    "U.S.-listed common stocks",
                    "ordinary shares",
                    "ADRs",
                ],
                "unsupported": ["forex", "commodities", "crypto", "other non-equity assets"],
                "universe_source": (
                    "Nasdaq Trader symbol directories, filtered by the existing "
                    "scanner's U.S.-listed equity rules"
                ),
                "price_data_source": "Yahoo Finance chart data",
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
    return _api_error(
        "interpretation_requires_result",
        "Run Python research first, then use Interpret with AI on that result.",
        410,
    )


@app.post("/api/scanner")
def scan():
    body, error = _json_body()
    if error:
        return error
    mode = body.get("mode", "auto")
    symbols = body.get("symbols", list(DEFAULT_SCAN_SYMBOLS))
    if mode not in ("auto", "daily", "orb"):
        return _api_error("invalid_mode", "Choose automatic, daily, or opening-range scan.", 400)
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
    normalized = []
    valid_symbols = []
    invalid_symbols = []
    duplicate_symbols = []
    seen = set()
    for raw_symbol in symbols:
        symbol = raw_symbol.strip().upper()[:MAX_SYMBOL_LENGTH]
        if not symbol:
            continue
        if symbol in seen:
            duplicate_symbols.append(symbol)
            continue
        seen.add(symbol)
        normalized.append(symbol)
        (valid_symbols if TICKER_RE.fullmatch(symbol) else invalid_symbols).append(symbol)
    if not normalized:
        return _api_error("invalid_symbols", "Enter at least one ticker symbol.", 400)

    mode_reason = None
    if mode == "auto" and valid_symbols:
        mode, mode_reason = _automatic_scan_mode()
    elif mode == "auto":
        mode = "daily"
        mode_reason = "No valid ticker was supplied, so no market-data request was made."
    request_text = "Scan " + " ".join(valid_symbols or normalized)
    tool = "scanner_daily" if mode == "daily" else "scanner_orb"
    if valid_symbols:
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
        result, warnings = _execute_validated_action(
            tool, {"symbols": valid_symbols}, request_text
        )
    else:
        result = {
            "id": secrets.token_urlsafe(12),
            "tool": tool,
            "status": "completed",
            "data": {
                "created_by": "trader-scanner",
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "mode": mode,
                "universe": 0,
                "scanned": 0,
                "failed": 0,
                "source": "yahoo",
                "hits": [],
                "symbol_results": [],
            },
        }
        warnings = []
    result["requested_symbols"] = normalized
    _structure_scan_result(
        result,
        normalized,
        valid_symbols,
        mode,
        invalid_symbols,
        duplicate_symbols,
    )
    if mode_reason:
        result["scan_mode_reason"] = mode_reason
    if warnings:
        result["runtime_input_warnings"] = warnings
    result["research_id"] = _remember_research(result) if valid_symbols else None
    return jsonify(result)


@app.post("/api/instrument")
def analyze_instrument():
    body, error = _json_body()
    if error:
        return error
    symbol, error = _single_symbol(body)
    if error:
        return error
    allowed, retry_after = _limiter.allow(
        ("scanner", _remote_key()), MAX_SCANS_PER_WINDOW, SCAN_WINDOW_SECONDS
    )
    if not allowed:
        response = jsonify(
            {
                "error": {
                    "category": "rate_limited",
                    "message": "Too many market requests. Wait before trying again.",
                }
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    request_text = "Analyze the available quote data for " + symbol
    result, warnings = _execute_validated_action(
        "alerts_quote", {"symbols": [symbol]}, request_text
    )
    result["symbol"] = symbol
    if warnings:
        result["runtime_input_warnings"] = warnings
    result["research_id"] = _remember_research(result)
    return jsonify(result)


@app.post("/api/research/interpret")
def interpret_research():
    body, error = _json_body()
    if error:
        return error
    research_id = body.get("research_id")
    if set(body) != {"research_id", "symbol"}:
        return _api_error(
            "invalid_request",
            "Choose one completed symbol result to interpret.",
            400,
        )
    if not isinstance(research_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{24,64}", research_id
    ):
        return _api_error("invalid_research_id", "Choose a recent research result first.", 400)

    allowed, retry_after = _limiter.allow(
        ("research_interpret", _remote_key()),
        MAX_REQUESTS_PER_WINDOW,
        ANALYSIS_WINDOW_SECONDS,
    )
    if not allowed:
        response = jsonify(
            {
                "error": {
                    "category": "rate_limited",
                    "message": "Too many AI interpretations. Wait before trying again.",
                }
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    now = time.monotonic()
    with _research_contexts_lock:
        context = _research_contexts.get(research_id)
        if context and now - context["created"] > RESEARCH_CONTEXT_TTL:
            _research_contexts.pop(research_id, None)
            context = None
    if not context:
        return _api_error(
            "research_context_expired",
            "That result is no longer available for AI interpretation. Run the research again.",
            410,
        )
    try:
        evidence = context["evidence"]
        symbol, error = _single_symbol(body)
        if error:
            return error
        expected, symbol_evidence = _research_interpretation_input(evidence, symbol)
        interpretation = coordinator.interpret_research_result(
            symbol_evidence,
            expected,
            deadline=time.monotonic() + coordinator.MAX_TOTAL_SECONDS,
        )
    except coordinator.provider.ProviderUnavailable as exc:
        return jsonify(
            {
                "ai_status": "unavailable",
                "message": "AI interpretation is unavailable. The original Python result is unchanged.",
                "attempts": exc.attempts,
            }
        )
    except ValueError as exc:
        return _api_error("invalid_research_result", str(exc), 422)
    return jsonify(
        {
            "ai_status": "complete",
            "interpretation": interpretation,
            "provider": interpretation.get("provider"),
            "model": interpretation.get("model"),
        }
    )


@app.post("/api/risk")
def check_risk():
    body, error = _json_body()
    if error:
        return error
    if body:
        return _api_error("invalid_request", "This check uses the saved order and risk files.", 400)
    if not (
        _has_regular_runtime_file("orders.json")
        and _has_regular_runtime_file("config.json")
    ):
        return _api_error(
            "risk_inputs_missing",
            "Add proposed orders and risk limits before running a risk check.",
            409,
        )

    request_text = "Check the configured proposed orders against the saved risk limits"
    result, warnings = _execute_validated_action("risk_check", {}, request_text)
    if result.get("status") == "completed":
        result = _interpret_action(result, request_text)
    if warnings:
        result["runtime_input_warnings"] = warnings
    status_code = 200 if result.get("status") == "completed" else 502
    return jsonify(result), status_code


@app.post("/api/providers/check")
def check_provider_connection():
    body, error = _json_body()
    if error:
        return error
    if body:
        return _api_error("invalid_request", "This connection check does not accept options.", 400)
    allowed, retry_after = _limiter.allow(
        ("provider_check", _remote_key()),
        MAX_REQUESTS_PER_WINDOW,
        ANALYSIS_WINDOW_SECONDS,
    )
    if not allowed:
        response = jsonify(
            {
                "error": {
                    "category": "rate_limited",
                    "message": "Too many connection checks. Wait before trying again.",
                }
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    try:
        result = coordinator.provider.chat_completion(
            [
                {
                    "role": "system",
                    "content": "Reply with the single word OK.",
                },
                {
                    "role": "user",
                    "content": "GE Nexus connection check. Reply OK.",
                },
            ],
            deadline=time.monotonic() + coordinator.MAX_TOTAL_SECONDS,
            validate_content=_validate_provider_check_response,
        )
    except coordinator.provider.ProviderUnavailable as exc:
        attempts = exc.attempts
        _remember_provider_check(attempts)
        categories = {
            "missing_key": "unavailable",
            "missing_model": "model_not_configured",
            "authentication": "connection_failed",
            "invalid_model": "model_unavailable",
            "invalid_request": "invalid_request",
            "insufficient_credits": "insufficient_credits",
            "rate_limited": "temporarily_unavailable",
            "provider_outage": "temporarily_unavailable",
            "timeout": "temporarily_unavailable",
            "connection_error": "temporarily_unavailable",
            "deadline_exceeded": "temporarily_unavailable",
            "invalid_configuration": "connection_failed",
            "invalid_configuration_or_request": "model_unavailable",
        }
        return (
            jsonify(
                {
                    "verified": False,
                    "message": "No configured provider completed the connection check.",
                    "attempts": attempts,
                    "provider_results": [
                        {
                            "provider": item.get("provider"),
                            "state": categories.get(item.get("category"), "connection_failed"),
                        }
                        for item in attempts
                    ],
                }
            ),
            503,
        )
    _remember_provider_check(
        result.get("attempts", []),
        verified_provider=result["provider"],
        verified_model=result["model"],
    )
    return jsonify(
        {
            "verified": True,
            "provider": result["provider"],
            "model": result["model"],
            "attempts": result.get("attempts", []),
        }
    )


if __name__ == "__main__":
    # Development only. Deployment should use the documented Gunicorn command.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
