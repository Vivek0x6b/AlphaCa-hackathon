# Hermes Agent Integration

AlphaCa's core trading logic (signal detection, backtesting, sizing,
execution, exits, autonomous re-tuning) runs entirely on plain Python
calling Alpaca directly via `alpaca-py` — see the main README. That
system has zero dependency on any LLM and is what actually places trades.

Hermes Agent (running the `nemotron-3-ultra-550b-a55b` model, connected to
Alpaca's MCP server, satisfying the hackathon's MCP/CLI requirement) adds
a second, independent layer on top: daily narration of what the trading
agent did and why, in plain English, with zero human involvement.

## Why a separate layer, not one combined system

Keeping trading execution and LLM narration separate was a deliberate
design choice (see `docs/designs/autonomous-backtest-retuning.md`,
Approach C): the LLM never makes a trading or parameter-tuning decision
itself. It only narrates a decision a deterministic, testable process
already made. This avoids betting the strategy's real risk on an LLM's
live judgment, while still giving genuine LLM-driven autonomy in the
demo — Hermes decides nothing, but explains everything, unattended.

## The daily narration job

A Hermes cron job (`alphaca-daily-narration`) runs unattended, daily,
Monday-Friday at 4:35pm ET — 20 minutes after the trading scheduler's
4:15pm run, giving the autonomous re-tune job time to finish first.

- **Workdir**: the project root, so it can read `logs/journal.jsonl`
  directly via its own terminal access (no custom MCP tool needed).
- **Task**: read that day's journal entries, explain any parameter
  re-tune decision (with the real statistical evidence — mean P&L
  difference, p-value, trade count), and narrate the story of any trade
  that fired (signal → spread selected → order → outcome).
- **Alpaca MCP skill loaded**: can also pull live account state (current
  positions, equity) to cross-reference against the journal.

Verified working live, including correctly handling a real NVIDIA API
outage (three retries) and correctly scoping "today" across a midnight
boundary rather than reporting stale data as current.

## `scripts/daily_summary.py` — deterministic extraction, not LLM parsing

Originally the cron job's prompt asked Hermes to read the raw
`journal.jsonl` and filter it to today's entries itself. That works, but
it means the LLM is responsible for correctly parsing and date-filtering
JSON every single day — reliable so far, but it gets more expensive and
more error-prone as the journal grows over the competition week, and any
mistake there is invisible (the LLM would just narrate whatever it
thinks it found).

`scripts/daily_summary.py` does that filtering deterministically instead:
plain Python, testable, always correct. It reads `logs/journal.jsonl`,
extracts only today's entries, and prints a clean structured JSON summary
(today's re-tune decision if any, and the full story of any ticker that
fired: signal → spread → sizing → order). Hermes' cron job runs this as
a pre-step (the `script` field) and only has to turn already-correct
structured data into readable prose — it never touches raw log parsing.

## Setting it up again (e.g. on a new machine)

```
cronjob(
  action="create",
  name="alphaca-daily-narration",
  schedule="35 16 * * 1-5",
  workdir="<path to this repo>",
  script="scripts/daily_summary.py",
  prompt="The attached JSON is today's structured summary from AlphaCa,
    an autonomous options trading agent on Alpaca (produced by
    scripts/daily_summary.py, already filtered to today and already
    correct - do not re-derive it). Turn it into a short, clear markdown
    report a judge could read in under a minute: (1) the re-tune
    decision, if any - what changed, why, and the statistical evidence
    (or why nothing changed). (2) for each fired ticker, the full trade
    story: what triggered the signal, the spread selected, the sizing,
    and whether an order was placed. (3) if fired_tickers is empty, just
    note which tickers were checked and that nothing fired.",
  skills=["alpaca"],
  deliver="origin",
  continuity=true
)
```
