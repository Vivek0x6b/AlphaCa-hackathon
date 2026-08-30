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
   moving average), and require volume at least 1.5x the 20-day average.
2. **Options selection** — when a signal fires, pull the option chain and
   pick a long/short strike pair (by delta) 2–4 weeks out to build a debit
   spread.
3. **Execution** — place the spread as a real multi-leg order via Alpaca's
   Trading API, sized to a fixed percent of account equity.
4. **Position management** — track open positions and exit on a profit
   target, stop loss, or thesis invalidation.
5. **Journal** — every signal check and trade decision is logged with the
   reasoning behind it (`logs/journal.jsonl`).

A Python scheduler (`scripts/scheduler.py`) runs the loop once daily after
market close, unattended, checking Alpaca's real trading calendar so it
correctly skips weekends and holidays.

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
- The calls-only version backtests at **+4.07%** over the same window, after
  also tuning the short leg's delta range and stop-loss threshold based on
  statistically significant backtested improvements.

A daily autonomous re-tune job (`scripts/retune.py`) re-runs the backtest on
the expanding dataset and only adopts a parameter change when it clears a
real significance test (paired comparison against the current parameters,
not just "the new number is bigger") — no human approval step. It already
found and adopted a real improvement (tightening the stop loss from 50% to
40%) on its first live run.

## Stack

- **Alpaca Trading API** (`alpaca-py`) — market data, options chains, order
  execution, position management
- **Python** — signal logic, backtesting, options selection, execution,
  journaling, scheduling
- **Hermes Agent + NVIDIA NIM** (`nemotron-3-ultra-550b-a55b`) — reads the
  journal and narrates the day's decisions and the re-tune job's reasoning
  in plain English, on its own daily schedule

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
src/options_selector.py      chain filtering, strike & expiry selection
src/execution.py             position sizing, order payload
src/position_manager.py      exit logic
src/market_data.py           Alpaca market data (bars, chains, quotes)
src/broker.py                Alpaca account/position/order actions
src/black_scholes.py         option pricing for the backtester
src/strategy_params.py       mutable, autonomously-tunable parameters
src/trade_store.py           local record of currently open trades
src/journal.py                decision logging
scripts/run_agent.py         one live agent-loop pass
scripts/backtest.py          backtest engine against real history
scripts/retune.py            daily autonomous re-tune job
scripts/scheduler.py         runs the loop daily, unattended
docs/designs/                design docs and methodology notes
```

## Disclaimer

Built for the Alpaca AI Trading Agents Hackathon. Paper trading only — for
educational and demonstration purposes, not investment advice.
