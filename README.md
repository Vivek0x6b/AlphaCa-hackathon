# AlphaCa

An autonomous AI trading agent for Alpaca's **Options Alpha** hackathon track.

AlphaCa scans a watchlist for confirmed momentum breakouts, applies a trend and
volume filter, and — when a clear thesis is confirmed — executes a defined-risk
options debit spread through Alpaca's Trading API. Every decision (trade or no
trade) is logged with the reasoning behind it, so the strategy stays explainable
end to end.

Built on Alpaca's paper trading environment. No real money is involved.

## How it works

1. **Signal detection** — for each ticker in the watchlist, check for a
   20-day high/low breakout, confirm the move is with the trend (50-day
   moving average), and require volume at least 1.2x the 20-day average.
2. **News veto** — before trading a fired signal, an LLM (NVIDIA Nemotron)
   checks recent news for a material red flag (earnings miss, exec
   departure, lawsuit) that a pure price/volume breakout wouldn't catch.
   It can only block a trade the deterministic signal already generated,
   never originate one — same "narrates/gates, never decides" boundary as
   the rest of the LLM usage in this project.
3. **Options selection** — when a signal clears both checks, pull the
   option chain and pick a long/short strike pair (by delta) 2–4 weeks out
   to build a debit spread.
4. **Execution** — place the spread as a real multi-leg order via Alpaca's
   Trading API, sized to a fixed percent of account equity.
5. **Position management** — track open positions and exit on a profit
   target, stop loss, or thesis invalidation. Handles any number of open
   legs per ticker (not just a clean one-long-one-short pair), since a
   real duplicate-entry incident proved that assumption doesn't always
   hold — see `docs/incident-log.md`.
6. **Journal** — every signal check and trade decision is logged with the
   reasoning behind it (`logs/journal.jsonl`).
7. **Health check** — runs at the end of every cycle: confirms the
   scheduled run itself succeeded, and cross-checks local trade tracking
   against Alpaca's actual account state for drift.

A Windows Scheduled Task runs `scripts/daily_run.py` once daily after market
close, checking Alpaca's real trading calendar so it correctly skips
weekends and holidays. Deliberately not a long-running process (an earlier
version was) — a scheduled task survives machine reboots on its own and
always starts with fresh code, where a long-running process does neither.

## Backtested, evidence-driven strategy

The strategy isn't just coded logic — it's been validated and tuned against
real historical data (`scripts/backtest.py`, using Black-Scholes to price
options since Alpaca has no historical option chain data; see
`docs/designs/autonomous-backtest-retuning.md` for the full methodology):

- The original bidirectional (calls + puts) version backtested at **-9.40%**
  over ~2 years. Investigation found puts underperformed calls across 5 of 7
  watchlist tickers independently — not a fluke in one name.
- **Puts are disabled** (`PUT_TRADING_ENABLED = False`) as a result. They
  still fire and get journaled for transparency, just aren't traded.
- The calls-only version backtests at **+32.27%** over the same ~2-year
  window (138 trades, 40.6% win rate), after tuning the short leg's delta
  range, stop-loss threshold, and watchlist/volume-threshold based on
  statistically significant backtested improvements.

A daily autonomous re-tune job (`scripts/retune.py`) re-runs the backtest on
the expanding dataset and only adopts a parameter change when it clears a
real significance test (paired comparison against the current parameters,
not just "the new number is bigger") — no human approval step. Its
comparison logic had a real bug for its first ~week live (it paired trades
by list position, which silently broke whenever a parameter change shifted
total trade count — nearly always); fixed to pair by ticker+entry-date
instead, after which it immediately found and adopted a real improvement
(narrowing the short leg's delta range). See `docs/incident-log.md`.

## Stack

- **Alpaca Trading API** (`alpaca-py`) — market data, options chains, news,
  order execution, position management
- **Python** — signal logic, backtesting, options selection, execution,
  journaling, scheduling
- **NVIDIA Nemotron** (`nemotron-3-ultra-550b-a55b`) — two separate uses,
  kept deliberately apart: (1) the live news-veto gate on fired signals,
  which can only block a trade, never place one; (2) via Hermes Agent,
  reads the journal and narrates the day's decisions and the re-tune job's
  reasoning in plain English, on its own daily schedule. Neither ever
  originates a trading or parameter decision.
- **Windows Task Scheduler** — runs the daily cycle unattended, survives
  reboots

## Setup

```bash
git clone <this-repo>
cd AlphaCa
pip install -r requirements.txt
```

Copy your Alpaca paper trading credentials into an env file (kept **outside**
version control — see `.gitignore`):

```
ALPACA_API_KEY=your_key_id
ALPACA_SECRET_KEY=your_secret_key
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

## Project layout

```
config/watchlist.py         watchlist and signal parameters
src/signals.py               breakout / trend / volume detection
src/news_veto.py             LLM news-check gate on fired signals
src/market_regime.py         market-wide trend filter (backtested, not adopted live)
src/options_selector.py      chain filtering, strike & expiry selection
src/execution.py             position sizing, order payload
src/position_manager.py      exit logic
src/market_data.py           Alpaca market data (bars, chains, quotes, news)
src/broker.py                Alpaca account/position/order actions
src/black_scholes.py         option pricing for the backtester
src/strategy_params.py       mutable, autonomously-tunable parameters
src/trade_store.py           local record of currently open trades
src/journal.py                decision logging
scripts/run_agent.py         one live agent-loop pass
scripts/backtest.py          backtest engine against real history
scripts/retune.py            daily autonomous re-tune job
scripts/daily_run.py         single run, invoked by the Windows Scheduled Task
scripts/health_check.py      end-of-cycle consistency and status check
scripts/daily_summary.py     structured journal extraction for Hermes narration
docs/designs/                design docs and methodology notes
docs/incident-log.md         real bugs found and fixed running this live
```

## Disclaimer

Built for the Alpaca AI Trading Agents Hackathon. Paper trading only — for
educational and demonstration purposes, not investment advice.
