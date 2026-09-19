# Strategy scorecard

Every strategy change tested against real backtested evidence, whether it
got adopted or not. The point of tracking rejected ideas here, not just
wins, is the same reason `docs/incident-log.md` exists: an evidence-driven
project should be able to show its failures as clearly as its successes.

## Adopted — currently live

| Change | Evidence | Date |
|---|---|---|
| Puts disabled | Bidirectional backtest: -9.40%. Puts underperformed calls across 5/7 tickers independently (avg -4.36% vs +2.64% forward return). Calls-only: positive. | pre-2026-09-02 |
| Watchlist widened 7 → 13 tickers | Original watchlist fired only ~1 signal/13 trading days - under 50/50 odds of firing during a short window. Same quality bar (liquid mega-caps/ETFs), not a looser thesis. | 2026-09-01 |
| Volume threshold 1.5x → 1.2x | 4-threshold sweep: 1.2x gave both more signals (172 vs 79) AND higher avg 14-day forward return (+1.68% vs +0.90%) than 1.5x - not a tradeoff, better on both axes. | 2026-09-01 |
| Short leg delta (0.15,0.20) → (0.10,0.15) → (0.05,0.10) | Each step backtested better: +3.09% → +4.07% → (see retune fix below) +32%+ on the current baseline. Wider short strike = more room for the long leg to run. | 2026-09-01, 2026-09-11 |
| Position sizing 2% → 8% | Backtested: same 133-134 trades, same 44-45% win rate, whole P&L distribution scales up together (2yr return +14.74% → +40.83%). No red flags (contract counts, worst-case drawdown all scaled proportionally, not explosively). | 2026-09-03 |
| Retune significance test fixed (pair by ticker+date, not list position) | The bug meant it could never adopt anything (comparison silently failed whenever trade count differed between baseline and candidate, which was nearly always). Fixed, then immediately found and adopted the delta-range improvement above on its first real run. | 2026-09-11 |
| News veto layer | Not "adopted via backtest" (can't backtest live news) - validated live instead: correctly identifies no-red-flag cases on real fired signals (AMD, META), fails open on error so it can never block trading uptime. | 2026-09-09 |
| Multi-leg exit handling (any number of legs per ticker) | Fixed a real bug where a ticker with 2 long + 2 short legs (from the duplicate-entry incident) got the wrong P&L and failed to close (quantity mismatch). Now aggregates correctly. | 2026-09-12 |
| GTC close orders (not DAY) | Confirmed live: DAY close orders silently expired at the next session's open without ever attempting to fill, letting a stop-loss run from -40% to -67% before finally executing. GTC actually rests through the session. | 2026-09-14 |
| Intraday exit-checks (every 15min) | Live design decision, not backtested in isolation - but exits are based on live position marks with no dependency on waiting for a daily close, so checking more often can only catch a target/stop sooner, never later. Confirmed live: caught META's profit target same-hour instead of waiting for the once-daily 4:15pm check. | 2026-09-16 |

## Tested and rejected — real evidence, not adopted

| Idea | Evidence against it | Date |
|---|---|---|
| Market-wide regime filter (only trade calls when SPY > its own N-day MA) | Looked good at one MA window (200-day: +27% vs +20% baseline) but wildly inconsistent across nearby windows (100-day: -2.68%, 150-day: +1.76%, 252-day: back to baseline). Picking 200-day specifically would have been cherry-picking a lucky parameter. | 2026-09-11 |
| Trailing stop instead of flat profit target (let winners run, exit on pullback from peak) | Tested twice, two different ways. Daily-checked: +20.44% baseline → -33.48%. Retested hourly-checked (to rule out "check frequency was the real problem"): still -20.55% at a 20% trail, only beating baseline at a very tight 10% trail (+5.56% vs +9.34%). The leveraged, decaying option structure genuinely doesn't reward "letting it run" the way a plain stock trend-follow would. | 2026-09-11, 2026-09-17 |
| Naked long call (skip the short leg entirely) | Backtested genuinely better (+20.44% → +73.29%, same win rate), but also deepened the worst-case single-trade loss by ~55% (-$10,857 → -$16,899). Explicit decision: not adopted, kept the defined-risk spread structure as-is. | 2026-09-11 |
| Spot crypto strategy (BTC/ETH/SOL, later 13 coins) | Reused equity breakout logic against real crypto history (hourly + daily). Every variant tried - frequency separation, invalidation buffer, longer breakout windows (55-100 days), wider watchlist, profit/stop ratio sweeps - capped out near breakeven (best case +0.83% on 4 trades, not statistically meaningful) or went negative. No configuration showed a real, demonstrated edge. Dropped rather than ship something unproven. | 2026-09-16 |

## Known limitations, not yet resolved

- The options backtest prices options via Black-Scholes off real stock
  prices (Alpaca has no historical option chain data), with a trailing-
  realized-vol input and a flat slippage haircut - a reasonable
  approximation, not real historical option fills.
- Live sample size is still small (4 real positions as of 2026-09-17) -
  not enough to independently validate the backtest's ~44% win rate yet.
