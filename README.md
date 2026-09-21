# AlphaCa

An autonomous AI trading agent for Alpaca's **Options Alpha** hackathon track.

<!-- PNL_DASHBOARD_START: auto-regenerated daily by scripts/update_readme_dashboard.py - do not hand-edit between these markers, it will be overwritten -->
<p align="center">
  <img src="https://img.shields.io/badge/return-%2B15.35%25-0ca30c?style=for-the-badge" alt="Total return +15.35%">
  <img src="https://img.shields.io/badge/P%26L-%2B%2415%2C353-0ca30c?style=for-the-badge" alt="Realized P&L">
  <img src="https://img.shields.io/badge/win_rate-40%25-2a78d6?style=for-the-badge" alt="Win rate 40%">
  <img src="https://img.shields.io/badge/trades_closed-5-2a78d6?style=for-the-badge" alt="5 trades closed">
  <img src="https://img.shields.io/badge/status-autonomous-0ca30c?style=for-the-badge" alt="Status: autonomous">
</p>

<p align="center">
  <img src="docs/equity_curve.png?v=2026-09-21" alt="AlphaCa account equity over time, currently $115,348" width="800">
</p>
<!-- PNL_DASHBOARD_END -->

<p align="center">
  <sub>Real paper-account equity, reconstructed from Alpaca fill history — see <a href="docs/trade-ledger.md">the full trade ledger</a> and the <a href="docs/dashboard.html">interactive dashboard</a> (download and open locally, or view live if GitHub Pages is enabled for this repo).</sub>
</p>

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
   hold — see `docs/incident-log.md`. Checked every 15 minutes during
   market hours (`scripts/intraday_exit_check.py`), not just once daily —
   a profit target hit mid-morning gets closed then, not hours later.
   Entry signals stay on the once-daily cycle, since the breakout/trend
   logic is genuinely defined against a confirmed daily close.
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
src/factors.py                candidate alpha factor library (momentum, RSI, volatility, etc.)
src/composite_signal.py      IC-weighted multi-factor entry signal (backtested, not adopted live)
src/atr_stops.py              ATR-adaptive exit thresholds (backtested, not adopted live)
src/kelly_sizing.py           Kelly-criterion position sizing (backtested; open risk-vs-return question, not adopted live - see docs/strategy-scorecard.md)
src/vol_spike_veto.py         entry veto on a short-term volatility spike (backtested, rejected)
src/market_vol_sizing.py      market-wide volatility-regime size scaling (backtested, rejected)
src/circuit_breaker_sizing.py  size reduction after a losing streak (backtested, no meaningful effect)
src/conviction_sizing.py      signal-strength-scaled sizing (backtested, rejected - see docs/strategy-scorecard.md)
scripts/factor_research.py   computes each factor's Information Coefficient against real forward returns
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
scripts/intraday_exit_check.py  frequent (15min) exit-only check during market hours
scripts/daily_summary.py     structured journal extraction for Hermes narration
docs/designs/                design docs and methodology notes
docs/incident-log.md         real bugs found and fixed running this live
docs/trade-ledger.md         every real position taken, entry/exit/P&L
docs/strategy-scorecard.md   every strategy change tested, adopted or rejected, with evidence
```

## Disclaimer

Built for the Alpaca AI Trading Agents Hackathon. Paper trading only — for
educational and demonstration purposes, not investment advice.
