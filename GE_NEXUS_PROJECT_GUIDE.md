# GE Nexus Project Guide

Last reviewed: 2026-10-29

This is the persistent handoff guide for GE Nexus. It records the verified
repository state, separates implemented behavior from future plans, and gives
future contributors a starting point without repeating the initial
investigation.

## Current status at a glance

- **Implemented and verified:** the stock-analysis Python CLI tools, their
  deterministic calculations, structured file handoffs, and offline fixture
  tests are present in this checkout. Stage 1 historically reported 315
  passing tests.
- **Implemented, not verified against live services:** code paths that
  retrieve public market, SEC, or House Clerk data. The Python broker-ticket
  tool records local ticket state but does not call a broker; a separately
  configured connector is described in existing Claude instructions, but was
  not verified or configured here.
- **Implemented and offline-tested:** the optional GE Nexus coordinator and
  ordered provider adapter. No provider or live market-data calls were made
  during Stage 2 verification.
- **Known limits:** public data can be delayed or unavailable; this repository
  is not a web application or a complete trading service.

The runtime `trader/` data directory is not present in the checked-in project
snapshot. Tools create or use those files when run.

## 1. Project identity

- Project name: **GE Nexus**.
- Origin: the public `day-trader-skill` repository:
  https://github.com/Jakeschincariol/day-trader-skill
- GE Nexus is an adaptation and continued development of that existing
  Python-based project, not a new application built from scratch.
- The repository retains its existing tool folders and paths. The upstream
  MIT license is in `LICENSE`; the Claude plugin identifier remains
  `day-trader-skill` for compatibility.
- The product is an AI-coordinated market-analysis and trading-research toolkit
  built around deterministic Python tools. The AI coordinator is optional; the
  existing Python commands remain usable without provider credentials.

At this review, the imported Git checkout is on `main` at `7b1d336`
(`Add project guide and update repository tests`). The tracked working tree
was clean before this documentation update. Git reports this as a shallow,
grafted repository, so the full parent history is unavailable. The Stage 1
report below names `f940c12`; that commit cannot be confirmed from the history
available in this checkout and is retained only as a historical report, not
the current HEAD.

The guide was already present in the imported snapshot. The root `.replit`
selects Python 3.12 but defines no run command, and no Replit workflow was
configured at review time. No root-level `AGENTS.md`, `replit.md`, or
`CONTRIBUTING.md` was found in the inspected project paths. `README.md` and
the eleven tool-specific `SKILL.md` files are the main usage documentation;
`.replit` contains environment configuration, not project instructions.

## 2. Vision and intended workflow

The coordinator supports a restricted version of the intended workflow:

1. Understand the user's market-analysis request.
2. Select and run the relevant existing Python tools.
3. Retrieve available market information.
4. Analyze candidates and potential setups.
5. Incorporate available news and fundamental information.
6. Evaluate entries and exits when required inputs exist.
7. Run historical simulations where appropriate.
8. Calculate proposed position sizing.
9. Run risk checks.
10. Combine the results into a clear explanation.

The coordinator does not automate every step in this sequence. It validates
each proposed action against a local allowlist and the existing CLI interface.
User-specific files are copied to a temporary workspace for a run; generated
files are discarded when that run ends.

## 3. What GE Nexus is and is not

### Implemented

- An existing Python CLI toolkit for stock research and trade planning.
- Core analysis that does not import or require Claude, Anthropic, OpenRouter,
  OpenAI, Grok/xAI, DeepSeek, or another AI provider.
- Deterministic calculations for detected setups, trigger status, entry/exit
  levels, simulations, sizing, and risk verdicts.
- Public-data retrieval code and local/offline fixture support where the
  individual command implements it.
- An optional AI provider adapter and coordinator that validate model plans,
  run allowlisted tools, and keep Python results separate from AI interpretation.

### AI coordinator scope

- Entry point: `python3 trader-ai/coordinator.py [--json] "request"`.
- Supported actions: daily and opening-range scans, news, entry checks, exit
  plans, historical simulation, exchange clock, quotes, SEC fundamental
  analysis, position sizing, and risk checks.
- The model can propose at most four actions. Local code validates plan schema,
  symbols, numeric ranges, allowed arguments, dependencies, and output types.
- Commands use fixed script paths, argument lists, `shell=False`, bounded
  timeouts and output, and a temporary working directory. Provider keys are
  removed from child-process environments.
- Broker-ticket, approval, fill, flatten, arbitrary shell, arbitrary code, and
  model-selected network actions are not in the allowlist.
- Provider priority is OpenRouter -> OpenAI -> Grok/xAI -> DeepSeek. Each
  provider requires both its API-key environment variable and its model-name
  environment variable. No model defaults are assumed.
- Default chat-completions endpoints are:
  `https://openrouter.ai/api/v1/chat/completions`,
  `https://api.openai.com/v1/chat/completions`,
  `https://api.x.ai/v1/chat/completions`, and
  `https://api.deepseek.com/chat/completions`. Optional HTTPS endpoint
  overrides use `<PROVIDER>_BASE_URL`.
- API key variables are `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `GROK_API_KEY`,
  and `DEEPSEEK_API_KEY`. Model variables are `OPENROUTER_MODEL`,
  `OPENAI_MODEL`, `GROK_MODEL`, and `DEEPSEEK_MODEL`. Keep credentials in
  Replit Secrets or another secure environment store, not in source files.
- Missing keys or model names skip that provider. Timeouts, connection errors,
  rate limits, server errors, invalid responses, and configuration failures
  move to the next provider. There is one bounded request attempt per provider.
- If all providers fail, the coordinator returns a structured unavailable
  result and the standalone Python CLIs remain usable.
- The model receives bounded results for interpretation. Tool outputs are
  treated as the factual source; AI statements are returned separately.

### Out of scope or not established

- GE Nexus is not a broker or brokerage account. A proposed signal, plan, or
  order ticket is not evidence that an order was placed or executed.
- Broker execution is outside the initial AI-coordinator scope.
- This is not a complete web application or guaranteed live-market-data
  service.
- Forex is not a complete supported feature in this stock-oriented toolkit.
- Delayed, stale, missing, simulated, or unavailable data must never be
  described as live and verified.

## 4. Existing Python toolkit

The commands below are verified in each script's argument parser. For full
flags and examples, read the corresponding `SKILL.md` and Python file before
changing or invoking a command.

| Tool | CLI interface | Responsibility |
|---|---|---|
| `trader-scanner/scan.py` | `universe`, `daily`, `orb` | Builds the symbol universe and detects rule-based daily setups and opening-range conditions. |
| `trader-entry/entry.py` | `check`, `watch`, `watchlist` | Evaluates bar-based entry conditions and reports waiting, triggered, expired, or unavailable states. |
| `trader-exit/exit.py` | `plan`, `show` | Calculates a stop, target, risk per share, and reward-to-risk from an entry or supplied values. |
| `trader-timemachine/timemachine.py` | `run`, `chart` | Runs historical resampling and same-rule history checks from a plan and bars. |
| `trader-alerts/alerts.py` | `clock`, `quote`, `check`, `add`, `list`, `reset` | Reads quotes and evaluates stored price/time alert rules. |
| `trader-news/news.py` | Flags such as `--tickers`, `--hours`, `--offline`, `--json` | Retrieves and filters news/filing items and calculates available price moves. |
| `trader-analyst/facts.py` | Ticker or CIK arguments, plus flags | Retrieves or reads SEC XBRL facts and computes financial metrics. |
| `trader-risk/check.py` | Flags such as `--orders`, `--config`, `--json`, `--no-write` | Checks proposed orders against deterministic risk rules and reports PASS/BLOCK. |
| `trader-budget/size.py` | Plan sizing; weights mode via `--weights` or `--targets` | Calculates position quantities and writes proposed order data when requested. |
| `trader-broker/ticket.py` | `show`, `approve`, `decline`, `result`, `fill`, `flatten`, `log` | Renders tickets and records local approval/result/fill state. The Python script does not call a broker. |
| `trader-pelosi/ptr.py` | `index`, `new`, `fetch`, `parse` | Reads and parses public House Clerk transaction disclosures. |

The analysis tools use ordinary Python for calculations and structured output.
AI should coordinate those tools and explain their results, not replace their
calculations or become a required dependency.

## 5. Original architecture and data flow

The repository contains Python scripts, skill instructions, example
configuration, and offline fixtures. It has no AI-provider client or central
Python process that calls the other scripts. Public-data requests are made by
the relevant data tool, not by an AI client.

Typical file handoffs are:

```text
scan.py universe -> trader/universe.txt
scan.py daily/orb -> trader/watchlist.json -> entry.py and news.py
entry.py -> trader/entry.json -> exit.py -> trader/plan.json
trader/plan.json -> timemachine.py, size.py, alerts.py, and news.py
alerts.py quote --write -> trader/prices.json -> size.py and risk/check.py
size.py -> trader/orders.json -> risk/check.py -> broker/ticket.py
```

The price-file handoff is conditional: `trader-alerts/alerts.py quote` writes
`trader/prices.json` only when requested with `--write`. Alert rules are
separately stored in `trader/state/alerts.json`. Other outputs include
`trader/timemachine.json`, optional `trader/metrics.json` from the analyst,
`trader/state/news.json`, `trader/state/risk.json`,
`trader/state/broker.json`, `trader/portfolio.json`, and Pelosi tracking state
under `trader/state/`. These are separate outputs, not evidence of a live
trade. Exact fields, nullable values, prerequisites, and readers must be
checked against the current writer and consumer code. The repository does not
define a shared formal JSON schema.

Configuration and data notes:

- `trader/config.json` holds user-supplied budget, risk, timing, and related
  settings. `trader-budget/config.example.json` is the example configuration.
- SEC tools use the existing `SEC_USER_AGENT` environment variable or
  `sec_user_agent` configuration when making SEC requests.
- Scanner, entry, exit ATR, Time Machine, alerts, news, analyst, and Pelosi
  tools have offline options in applicable commands. Budget and risk use local
  structured files.
- Existing output formats are part of the tool contract. Do not rename,
  replace, or casually reshape them.
- See `README.md`, especially its core-only usage section, for the verified
  command sequence and practical prerequisites.

## 6. Stage 1 accomplishments

The Stage 1 completion report says that the project identity and core use
without an AI API key were established. It lists these changes:

- `README.md`: GE Nexus branding, upstream attribution, and core-only usage
  instructions.
- `.claude-plugin/plugin.json`: updated user-facing description while
  preserving plugin identity and skill path.
- `.claude-plugin/marketplace.json`: updated project descriptions while
  preserving upstream identifiers.
- `tests/test_repo.py`: updated branding checks and repository-layout
  handling.

The report records these historical checks:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  completed with **315 tests passing**.
- `git diff --check` passed.
- No live market-data requests were made.
- No broker connection or real order submission occurred.
- No AI provider integration was added.

**Test caveat:** existing `tests/test_broker.py` flow tests write local
`placed` and fill statuses into temporary test files. Those records are
synthetic test state, not real broker executions. Do not describe local test
statuses as real trades. Future test selection must respect any explicit
instruction not to simulate an executed order.

These are historical Stage 1 results, not a guarantee that the current checkout
or later changes pass the suite. This guide update did not rerun the unit tests
or contact external services. The current imported checkout is shallow and
does not expose the report's cited `f940c12` commit; see the Git provenance
note above.

## 7. AI coordinator behavior

The coordinator is an optional layer over the existing Python tools. It:

- Uses one GE Nexus coordinator to handle request
  interpretation, tool selection, workflow coordination, and result
  explanation.
- Uses the provider priority OpenRouter -> OpenAI -> Grok/xAI -> DeepSeek, with
  bounded timeouts and one attempt per provider before moving to the next.
- Falls back on provider failures, rate limits, invalid responses, or timeouts.
- Reads credentials from runtime environment variables backed by Replit
  Secrets. Never hardcode, print, log, expose, or include secret values in
  diagnostic prompts.
- Uses a restricted allowlist of existing Python commands and validates
  symbols, arguments, results, and failures before presenting them.
- Passes structured tool results to the model for explanation. Python outputs
  remain the source of truth for prices, signals, plans, sizing, and risk.
- Keeps broker actions and external connectors outside the coordinator.
- Leaves the CLI analysis engine usable when all providers are missing,
  invalid, exhausted, or unavailable.

Without a working provider, AI-driven natural-language coordination and
explanations are unavailable unless a separate deterministic fallback
explicitly implements the requested workflow. Do not claim that the full AI
experience works without a model.

## 8. Future deployment direction

The intended order of work is:

1. Preserve the CLI and deterministic Python engine.
2. Implement and offline-test optional AI coordination and provider fallback.
3. Verify actual provider configuration with user-selected model identifiers
   and credentials, without exposing secrets.
4. Assess deployment on a suitable Python hosting environment.
5. Consider a user interface only in a later, separately approved stage.

No web application, live service, deployment, or production environment was
verified while preparing this guide.

## 9. Known limitations and risks

- Yahoo Finance data is unofficial and may be delayed, rate-limited, or
  unavailable. There is no live-data reliability guarantee.
- Other public data sources can also fail or change response formats.
- SEC requests require the existing identifying User-Agent configuration.
- Some commands need files produced by earlier steps.
- Quotes from different sources may differ in price or timestamp.
- A simulation is historical analysis, not a prediction.
- An identified setup is not necessarily a confirmed trade opportunity.
- The broker connector is external. The Python ticket script prepares and
  records local ticket state; it does not establish that an order was sent.
- The exact state of provider credentials, live connections, and deployment
  is unverified unless directly inspected in a later authorized task.

## 10. MANDATORY RULES FOR FUTURE AGENTS

1. Read `GE_NEXUS_PROJECT_GUIDE.md` before making changes.
2. Read the relevant existing code before proposing or implementing changes.
3. Treat the existing Python engine as the foundation. Do not rebuild the
   project from scratch.
4. Preserve strategies, CLI interfaces, output formats, and compatibility
   unless a necessary change is explicitly justified.
5. Avoid unnecessary dependencies, files, abstractions, and complexity.
6. Prefer minimal, complete, tested implementations.
7. Never hardcode or expose credentials.
8. Never claim that a test, live-data request, deployment, or broker operation
   succeeded unless it actually did.
9. Never equate a local simulated order status with a real broker execution.
10. Never perform real broker operations without separate explicit
    authorization and appropriate safeguards.
11. After each meaningful development stage, update this guide with actual
    changes, tests, failures, decisions, and remaining work.
12. Do not erase earlier milestone history. Keep a concise chronological
    milestone log and update the current status.
13. Use this guide as the initial handoff when transferring the project to
    another account, repository, or AI agent.
14. Do not ask the user to repeat information already recorded here.
15. Keep the user informed of actual changes and test results in plain
    language.

## 11. Current status and next steps

- Stage 1 foundation and branding are complete.
- The Python tools remain the core analysis engine and do not require AI
  credentials.
- Stage 2 coordinator and provider fallback are implemented and offline-tested.
- Provider model names must be supplied as environment variables; live provider
  requests, market-data requests, broker activity, and deployment were not
  tested.
- The next milestone is a controlled provider configuration check, followed by
  a separate deployment assessment.

## 12. Milestone log

| Stage | Work completed | Verification | Remaining work |
|---|---|---|---|
| Stage 1 - historical | GE Nexus identity, core-only README guide, plugin descriptions, and repository checks, as reported in the Stage 1 completion report. | The report records 315 passing unit tests and `git diff --check`; no live-data or broker operation was performed. Broker-flow tests use synthetic local placement/fill state. Results were not rerun for this guide update. | Continue preserving core interfaces; implement the optional AI coordinator only as a separately approved milestone. |
| Handoff guide - reviewed 2026-10-29 | The guide was already present in the imported snapshot; its status, Git provenance, and conditional price-file handoff were reconciled with the current files. | Documentation-only change; `git diff --check` passed. No unit tests, live-data requests, provider calls, or broker operations were run. | Keep this guide current after future development stages. |
| Stage 2 - optional AI coordinator | `trader-ai/provider.py` and `trader-ai/coordinator.py` provide ordered OpenRouter, OpenAI, Grok, and DeepSeek fallback; strict plan validation; bounded allowlisted CLI execution; and structured interpretation. `README.md` and this guide document invocation, configuration, and limits. `tests/test_ai_provider.py` and `tests/test_ai_coordinator.py` cover offline behavior. No strategy logic or broker utilities were changed. | Focused AI suite: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_ai_*.py' -v`, 44 passed. Broader offline suite discovered 359 tests, excluded all 18 `tests/test_broker.py` cases to avoid running ticket placement/fill state flows, and passed the remaining 341. All 11 coordinator CLI mappings parsed via `--help`. The repository checks passed: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_repo.py' -v` (11 passed) and `git diff --check`. No provider requests, live market-data requests, or broker operations were performed. | Test chosen model identifiers and provider credentials in a controlled provider check. Then assess deployment separately; no production readiness is claimed. |
| Deployment assessment - future | Nothing implemented or verified. | Not run. | Assess hosting after provider configuration and operational limits are separately verified. |
