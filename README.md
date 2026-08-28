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
3. **Execution** — place the spread as a multi-leg order via Alpaca's MCP
   server, sized to a fixed percent of account equity.
4. **Position management** — track open positions and exit on a profit
   target, stop loss, or thesis invalidation.
5. **Journal** — every signal check and trade decision is logged with the
   reasoning behind it.

The full loop runs on a schedule (via Hermes Agent's cron) so it operates
without manual intervention during market hours.

## Stack

- **Hermes Agent** — autonomous runtime, scheduling, decision logging
- **NVIDIA NIM** (`nemotron-3-ultra-550b-a55b`) — tool-calling LLM, free tier
- **Alpaca Trading API + MCP Server** — market data, options chains, order
  execution, position management
- **Python** — signal logic, options selection, execution, journaling

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
config/            watchlist and signal parameters
src/signals.py      breakout / trend / volume detection
src/options_selector.py   chain filtering, strike & expiry selection
src/execution.py    order placement via Alpaca
src/position_manager.py   exit logic, risk sizing
src/journal.py       decision logging
scripts/run_agent.py   main entry point / cron target
```

## Disclaimer

Built for the Alpaca AI Trading Agents Hackathon. Paper trading only — for
educational and demonstration purposes, not investment advice.
