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
| IC-weighted multi-factor composite signal (adapted from arXiv:2409.06289's evaluation methodology, human-authored factors instead of LLM-generated - see below) | Best case (threshold=1.0, volatility factor removed after diagnosing it as actively harmful - see note) reached +9.03% vs the existing breakout signal's +32.27% on the same 2yr data. A real methodology, tested properly, still didn't beat what's already live. | 2026-09-19 |
| ATR-adaptive profit-target/stop-loss (scale the fixed 50%/40% thresholds by each ticker's own recent volatility relative to the watchlist's typical volatility, adapted from arXiv:2604.27150) | Every clip range tested underperformed the fixed thresholds, and the pattern was monotonic: tighter clip (closer to no adaptation) -> closer to baseline (0.9-1.1x scale: -3.63%; wider clips: -11% to -14%), vs. the fixed thresholds' +32.27%. The already-tuned fixed values simply outperform any tested deviation from them in this direction - the retune job's own significance-gated process had already found a better answer than volatility-scaling could. | 2026-09-19 |
| Kelly-criterion position sizing (half-Kelly, walk-forward from the strategy's own trade history, adapted from arXiv:2508.18868) | See "A risk-vs-return tradeoff, not a clean rejection" below - not adopted, but for a different reason than the other rejected entries: the backtest number is lower (+2.11% vs +32.27%), but Kelly's own math says the current 8% sizing is 2-3x its recommended fraction given the strategy's actual win rate/payoff, which is a real risk signal the backtest's total-return comparison doesn't capture. | 2026-09-19 |
| Volatility-spike entry veto (skip a fired signal if very short-term realized volatility has spiked well above its own baseline, approximating the IV-crush-around-events literature, arXiv:1412.8414, without a dedicated earnings calendar) | Every threshold tested (1.3x-2.0x) underperformed baseline, from -13.36% to +1.63% vs the fixed signal's +32.27%. No threshold showed a real edge. | 2026-09-19 |
| Cap new entries to the single highest-relative-volume signal per day (avoid correlated same-day pile-on across the watchlist's mostly-correlated tech names) | Initially looked like a strong result (+79.40% vs +32.27% baseline, held up across two independent time-halves, and an arbitrary tie-break got nowhere close - real evidence the ranking criterion mattered). But checking nearby cap values exposed the same fragility that sank the market-regime filter: cap=1 -> +79.40%, cap=2 -> +11.16% (WORSE than no cap at all), cap=3 -> +30.18% (~matches baseline). A real structural effect should move smoothly as the cap loosens; this doesn't - it's a small number of specific trade exclusions swinging the total by tens of points on an already-small sample (138 trades), not a robust edge. Caught by direct user pushback ("are u sure about this?") after an initial validation pass that stopped one check short - a good reminder to run the same adjacent-parameter sensitivity check applied to the regime filter on every new idea, not just the ones that already "feel" edge-case-prone. | 2026-09-19 |
| Market-wide (SPY realized-vol percentile) position-size scaling - reduce size in high market-vol regimes, increase in calm ones, adapted from arXiv:2508.16598 / arXiv:2407.13908's VIX-percentile sizing | Clean, honest rejection this time - the relationship was smooth and monotonic across every multiplier range tested (mult range narrowing toward 1x/no-adjustment climbed steadily from -7.14% up through +3.92%, +10.61%, +17.22%, +23.06% toward the fixed-size baseline's +32.27%), confirming this is a real, coherent "doesn't help" rather than a fragile fluke like the entry-cap idea above. Notable: three different volatility-based ideas (per-ticker, as an entry driver / exit scaler / entry veto) and now a market-wide version (as a size scaler) have all been tested and all failed - a consistent pattern across very different mechanisms, not one unlucky implementation. | 2026-09-19 |
| Circuit-breaker sizing (shrink size after N consecutive losses, the other half of arXiv:2604.27150 not covered by the ATR-stops test) | Essentially a wash: best case (streak=3, reduction=0.25) reached +34.58%, barely above baseline's +32.27% on a ~138-trade sample - well within noise. More aggressive triggers (streak=1-2, or a bigger size cut) clearly hurt, in a coherent, monotonic way. Read as a real (if minor) finding in its own right: this strategy's wins/losses don't show meaningful streakiness worth exploiting. | 2026-09-19 |
| Conviction sizing (scale position size continuously by the fired signal's relative-volume strength, informed by the entry-cap experiment's finding that relative volume carries real information) | Passed the adjacent-parameter check (smooth, monotonic scaling from baseline +32.27% up to +78.97% as the multiplier range widened, with two exact sanity-check reproductions of baseline at the no-adjustment setting) but FAILED the split-half check: the entire improvement came from the first half of history (+47.13% -> +103.68%), while the second half moved in the OPPOSITE direction (+18.48% -> +8.13%, worse as the effect strengthened). A real edge should hold its direction in both independent halves even if the magnitude differs; this doesn't - it's a first-half-specific artifact (likely a handful of large high-relative-volume winners concentrated there), not a structural relationship. A useful demonstration that the adjacent-parameter check and the split-half check catch DIFFERENT failure modes - this idea would have been wrongly adopted if only the first check had been run. | 2026-09-19 |

## A genuine methodological finding, worth keeping even though rejected

Built `src/factors.py`, `scripts/factor_research.py`, and `src/composite_signal.py`
to reproduce the evaluation methodology (not the LLM-factor-generation step -
see below) from "Automate Strategy Finding with LLM in Quant Investment"
(arXiv:2409.06289): score several candidate factors (momentum, RSI,
realized volatility, relative volume, trend distance, Bollinger position)
by Information Coefficient against real forward stock returns, then
combine them into one weighted composite entry score.

The realized-volatility factor got the largest weight (IC +0.16, the
strongest of any factor tested) - high recent volatility genuinely
predicts positive forward *stock* returns (a mean-reversion bounce). But
this strategy doesn't trade the stock, it buys call options, and options
have their own extra sensitivity to volatility (vega) that a stock-return
IC test knows nothing about: buying calls when volatility is already
elevated risks paying inflated premium right before it reverts down (a
"vol crush"), which can lose money on the option even when the underlying
stock does exactly what the factor predicted. Confirmed directly: removing
just that one factor and rerunning turned a catastrophic -90.79% result
into +9.03% at the same threshold - the single biggest swing from any one
change tested in this project. Still short of the existing breakout
signal's +32.27% on the same data, so not adopted, but a real,
transferable lesson: any factor screened by IC-against-stock-returns needs
a second check against option-specific risk (like vega/IV exposure) before
it's trusted to gate an options trade, not just a stock trade.

## On not implementing the paper's LLM-generates-factors step

The paper's own ablation study (its Table 7) only tests removing the two
evaluation agents (Confidence Score, Risk Preference) - it never tests
"same factors, human-picked instead of LLM-generated" as a baseline. There
is no evidence in the paper that origination-by-LLM is what drives its
results, only that the selection/scoring methodology matters. Given that,
and that letting an LLM generate and execute arbitrary new formulas live
would reverse this project's tested "LLM narrates or vetoes, never
originates a trade" boundary for an unproven benefit, the factors here are
pulled from well-documented public quant literature instead - same breadth
of candidates, authored by a human.

## A risk-vs-return tradeoff, not a clean rejection

Kelly-criterion sizing (src/kelly_sizing.py) is different from the other
rejected experiments above: it isn't a backtest number that simply came
in worse for no good reason - it's a real disagreement about how much
risk the strategy's demonstrated edge actually justifies, and the
backtest's total-return metric can't settle that disagreement on its own.

Walking the strategy's own trade history forward (walk-forward, using
only trades closed before each point - no lookahead), Kelly's formula
consistently says the strategy's real win rate and win/loss payoff
justify risking roughly 3-10% of equity at full Kelly, or 1.5-5% at the
standard "half-Kelly" safety margin conservative practitioners use to
guard against noisy estimates - well below the 8% currently risked live.
That means the live strategy is currently betting at roughly 2-3x its own
full-Kelly-implied fraction.

Backtesting half-Kelly sizing over the same 2-year history returns
+2.11%, well below the fixed 8%'s +32.27%. That is an entirely expected
result and not evidence that 8% is the safer or better choice: betting
above Kelly can and often does produce a better total return on any one
specific historical path (which is exactly what happened here) while
still being a worse bet in expectation, because it also carries more
variance and a higher risk of a severe drawdown the specific path tested
didn't happen to hit. A backtest that only measures "which total return
was bigger on this one 2-year window" structurally can't distinguish
between those two explanations.

Not treating this as adopted or rejected - it's flagged here as a real,
unresolved risk question worth a deliberate decision (not a silent
default), separate from the purely evidence-losing experiments above.

## Known limitations, not yet resolved

- The options backtest prices options via Black-Scholes off real stock
  prices (Alpaca has no historical option chain data), with a trailing-
  realized-vol input and a flat slippage haircut - a reasonable
  approximation, not real historical option fills.
- Live sample size is still small (4 real positions as of 2026-09-17) -
  not enough to independently validate the backtest's ~44% win rate yet.
