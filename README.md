# GE Nexus

GE Nexus is a Python stock-analysis toolkit with eleven existing Claude skill instructions. Its core command-line tools calculate scans, entry and exit plans, simulations, position sizes, and risk checks without an AI API key.

The repository also includes an optional AI coordinator for those existing
Python tools.

The tools are plain Python that read public data from Yahoo Finance charts,
Nasdaq Trader's symbol lists, SEC EDGAR and the House Clerk. Core analysis does
not call an AI provider.

The repository remains a collection of Python command-line tools, with an
optional AI coordinator, not a web application or a complete trading service.
The scripts produce analysis and structured files; they do not replace a
user's review or guarantee a trade outcome.

The Python tools and AI coordinator do not submit orders. The existing broker
skill can prepare a ticket for a separately configured broker connector,
subject to its approval steps. Broker activity is outside the coordinator.

> **Upstream attribution:** GE Nexus is based on
> [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill).
> Its existing Python tools, skill paths, and MIT license are preserved. The
> original Trader skill, with 13F, stock screening, tax lots, and rebalancing,
> remains at [Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill).

## Existing Claude skill integration

The existing `trader-*/SKILL.md` files and plugin manifest describe how Claude
can invoke the command-line tools. This integration is optional for the Python
engine and does not add an AI provider API client.

Paste this into Claude:

```
https://github.com/Jakeschincariol/day-trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add Jakeschincariol/day-trader-skill
/plugin install day-trader-skill@day-trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/day-trader-skill:trader-scanner` and so on. Copy the folders instead if you want
plain `/trader-scanner`:

```bash
git clone https://github.com/Jakeschincariol/day-trader-skill.git
cp -r day-trader-skill/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. Without Claude Code, run the Python commands in the core
usage guide below directly from the repository.

The tools need Python 3.8 or newer. Nothing to pip install. The SEC tools
(news filings, the analyst) require an identifying name and email in
`trader/config.json` (`sec_user_agent`) or the `SEC_USER_AGENT` environment
variable. This is an SEC request identification requirement, not an AI API key.

## Optional AI coordinator

The coordinator can plan a request, validate it against a fixed allowlist of
existing tools, run those Python tools in a temporary workspace, and explain
their bounded results:

```bash
python3 trader-ai/coordinator.py "Check TSLA news and its entry setup"
python3 trader-ai/coordinator.py --json "Check the exchange clock"
```

Supported actions are daily and opening-range scans, news, entry checks, exit
plans, historical simulations, exchange-clock checks, quotes, SEC fundamental
analysis, position sizing, and risk checks. Some actions require saved files
from earlier tools. The coordinator asks for missing request details where
possible and reports tool prerequisites or failures; it does not submit orders
or call a broker. Public-data actions may contact their existing data sources.

AI providers are attempted in this order: OpenRouter, OpenAI, Grok, then
DeepSeek. Each provider needs both its API key and an explicitly configured
model name. Set these environment variables using Replit Secrets or another
secure environment-variable store, never in source files:

| Provider | API key | Model |
| --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_MODEL` |
| Grok | `GROK_API_KEY` | `GROK_MODEL` |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_MODEL` |

Choose a model identifier supported by your provider account. No model names
are assumed by default. Optional endpoint overrides are `OPENROUTER_BASE_URL`,
`OPENAI_BASE_URL`, `GROK_BASE_URL`, and `DEEPSEEK_BASE_URL`; they must be HTTPS
chat-completions endpoints. Default endpoints are OpenRouter
`https://openrouter.ai/api/v1/chat/completions`, OpenAI
`https://api.openai.com/v1/chat/completions`, xAI
`https://api.x.ai/v1/chat/completions`, and DeepSeek
`https://api.deepseek.com/chat/completions`.

Providers with missing keys or model names are skipped. Timeouts, rate limits,
server errors, and invalid responses move to the next configured provider.
If all providers are unavailable, the coordinator reports that AI assistance
is unavailable; the existing Python commands continue to work without AI
credentials. The provider request and tool execution are bounded, and model
output cannot select arbitrary commands, code, or broker actions.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans thousands of charts for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks breaking news on every stock you watch and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, so you know when to get out. |
| `/trader-timemachine` | Time Machine | Replays simulated sessions and historical occurrences of the same setup. |
| `/trader-risk` | Risk Manager | Checks proposed orders against configured limits and reports PASS or BLOCK. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade. |
| `/trader-broker` | Broker Tickets | Prepares and checks tickets for a separately configured broker connector. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits. |
| `/trader-analyst` | Earnings Analyst | Calculates changes in reported SEC XBRL financial metrics. |
| `/trader-pelosi` | Pelosi Tracker | Reads Congress's stock-trade disclosures the minute they are filed. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
news / analyst / pelosi                                                        (on a schedule)
```

This is a documented sequence, not a central Python coordinator. Run each
command yourself; its output files can be used by later commands.

An example, the trade in the reel: Tesla, Monday October 19 2026, real data.

1. **`/trader-scanner`** at 10:15 AM ET: of the 231 charts in the reel's
   scan, 42 had closed above their 15-minute opening range. TSLA is one: range 354.47 to 362.18,
   the 10:10 candle closed at 364.18, 0.55 % over the top.
2. **`/trader-news`**: the morning's TSLA headlines, in New York time
   ("Tesla-SpaceX Merger Mania Strikes Wall Street Ahead of Earnings", 8:04,
   tagged earnings and deal). Tesla reports this week: a day trade is out
   before the close, so it is not exposed to the report.
3. **`/trader-entry`** watched TSLA bar by bar. At 10:12 it said waiting (the
   10:10 bar was still forming). At 10:15 it said triggered: the first
   5-minute close above the range, 364.18.
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 358.32, so 5.86 a share at risk; target one times that risk,
   370.04; flat by 3:55 PM either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times, each
   5-minute candle drawn from TSLA's own candles at that time of day over the
   previous 59 sessions (seed 19, so anyone can repeat it): target first
   29.4 %, stop first 25.5 %, still open at the close 45.1 %. It also re-ran
   the same rule on those 59 real sessions: it fired 33 times, 13 hit the
   target, 15 the stop, 5 neither. Its verdict: close to a coin flip. That is
   the point of looking before you risk a dollar.
6. **`/trader-budget`** with a $5,000 account and a $50 max loss per trade:
   50 / 5.86 = 8 shares ($2,913.44). If the stop fills at 358.32, the loss is
   $46.88. It writes one bracket order: buy 8 TSLA, stop-loss 358.32,
   take-profit 370.04.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window, position
   size and cash. PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words. You type yes for
   that exact order. Claude calls your brokerage connector (Alpaca's MCP
   server, paper account by default), and Claude's own "Allow once / Deny"
   card asks you one more time.
9. **`/trader-alerts`** rings on the stop, the target and at 3:55 PM to
   flatten whatever is still open.

## Core-only usage (no AI API key)

The analysis commands below invoke the existing Python scripts directly and
do not require Claude or another AI provider. Commands that write results use
the repository's existing `trader/` JSON and text files.

1. Refresh the supported stock universe, then inspect its saved symbols:

   ```bash
   python3 trader-scanner/scan.py universe
   sed -n '1,20p' trader/universe.txt
   ```

   `universe` retrieves Nasdaq Trader symbol lists and writes
   `trader/universe.txt`. The scanner also accepts `--offline DIR` for saved
   symbol-list files and `--no-write` to avoid saving the result.

2. Run either the daily scan or the opening-range scan. Each writes
   `trader/watchlist.json`; running the second replaces the first scan's
   watchlist.

   ```bash
   python3 trader-scanner/scan.py daily
   python3 trader-scanner/scan.py orb --at 10:15
   ```

   `--at` pins the scan time in New York time. Review the watchlist with:

   ```bash
   python3 -m json.tool trader/watchlist.json
   ```

3. Check a symbol's existing opening-range setup rule:

   ```bash
   python3 trader-entry/entry.py check TSLA --setup orb --json
   ```

   This reports the current rule-based status. Add `--write` if you need
   `trader/entry.json` for the exit planner. Only continue to an exit plan
   when the entry result meets the tool's prerequisites, including a triggered
   entry. Use `--offline FILE` with a saved chart fixture/file when applicable.

4. Generate a plan from a qualifying saved entry:

   ```bash
   python3 trader-exit/exit.py plan --stop or-mid --target 1R --write
   ```

   This reads `trader/entry.json` and writes the existing
   `trader/plan.json` format. ATR stops can use `--offline FILE`; other stop
   types do not require bars for the stop calculation.

5. Run the historical Time Machine for an existing plan:

   ```bash
   python3 trader-timemachine/timemachine.py run --write
   ```

   This invocation reads `trader/plan.json`; supported CLI flags can supply
   plan values instead. Historical bars are required. Use `--offline FILE`
   with a saved Yahoo chart file where available. The simulation is a
   historical resampling exercise, not a prediction.

6. Size a proposed order and check its risk, when the inputs are configured:

   ```bash
   python3 trader-budget/size.py --write
   python3 trader-risk/check.py --json --no-write
   ```

   Sizing needs a plan and configured budget/risk settings; start from
   `trader-budget/config.example.json` when creating `trader/config.json`.
   Risk checking needs the proposed `trader/orders.json` and applicable
   configuration. The sizing script writes a proposal; neither command
   submits it. The risk command's `--no-write` option avoids recording a
   verdict in the runtime state file.

Some commands retrieve public data online. Applicable data tools support
`--offline` inputs, and the existing tests use local fixtures; not every
command has an offline mode. Public data availability and latency are not
guaranteed. Yahoo Finance data is unofficial and may be delayed; it is not
guaranteed real-time data. Detected setups are not confirmed trade
opportunities. These scripts are analysis tools, not financial advice or a
complete trading application.

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline on fixtures cut from real data: TSLA's 5-minute bars
for the 60 days to October 19 2026, the 10:15 AM opening-range scan, Yahoo's
TSLA headlines that morning, the House Clerk's 2026 index, real PTR text and
real XBRL facts. They reproduce every number in the example above.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Set your brokerage tool to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No options, margin, short
selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, and
Alpaca's MCP server defaults to its paper account. Keys live in environment
variables, never in the chat.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: an earnings report, a halt, a headline. Treat its odds as a
reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. The scanner and the entry
timer read 1-minute or 5-minute bars, not every tick. Check anything that
matters against your broker before you act on it.

**Day-trading rules.** FINRA removed the $25,000 pattern-day-trader minimum
on June 4, 2026 (Regulatory Notice 26-10), and brokers may phase the change
in until October 20, 2027, so yours may still apply the old rule. If it does,
set `"pdt_guard": true` and `/trader-risk` counts your day trades.

**Congress's trades are public, not inside information.** Members must disclose
trades over $1,000 within 45 days (the STOCK Act). The Pelosi skill reads those
public filings the minute they post.

**Taxes.** Day-trading gains are short-term gains, and frequent trading makes
wash sales likely. Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-pelosi/      SKILL.md, ptr.py
trader-ai/          optional provider adapter and restricted coordinator
tests/              unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).
