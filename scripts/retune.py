"""
Daily autonomous re-tune job.

Re-runs the backtest on the expanding historical dataset (not gated on live
trade count, since the strategy fires too rarely for that to be reliable -
see docs/designs/autonomous-backtest-retuning.md), tries small variations
around each currently-tunable parameter, and only adopts a change when it
clears a real statistical significance bar against the current parameters -
not just "the new number is bigger."

Scoped to exactly the parameters in src/strategy_params.py: delta ranges,
expiry window, exit thresholds. Never touches the watchlist, position
sizing, or whether puts are traded at all - those stay human decisions.

No human approval step: this is meant to run unattended as part of the
daily cycle. Every decision (a swap, or "no change") is logged to the
journal with its full reasoning, the same audit-trail style used for
trades.
"""

import sys
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.watchlist import WATCHLIST
from src.market_data import fetch_bars
from src.strategy_params import load_params, save_params
from src.journal import log_entry
from scripts.backtest import run_backtest, BACKTEST_LOOKBACK_DAYS

# Below this many paired trades, there's not enough evidence to trust any
# comparison, so no change is ever adopted regardless of the numbers.
MIN_TRADES_FOR_SIGNIFICANCE = 15

SIGNIFICANCE_LEVEL = 0.05

# Small neighborhood of alternatives tried around each current value, one
# parameter at a time (all others held at their current value). Chosen to
# match the scale of the manual sweep that found the current defaults.
CANDIDATE_STEPS = {
    "long_leg_delta_range": [(0.35, 0.45), (0.45, 0.55)],
    "short_leg_delta_range": [(0.05, 0.10), (0.15, 0.20)],
    "min_days_to_expiry": [7, 21],  # paired with max_days_to_expiry below
    "profit_target_pct": [0.40, 0.60],
    "stop_loss_pct": [0.40, 0.60],
}
# min/max expiry move together (a real window, not two independent knobs).
EXPIRY_WINDOW_CANDIDATES = [(7, 14), (21, 35)]


def _paired_significance(baseline_pnls: list[float], candidate_pnls: list[float]):
    """
    Paired z-test (normal approximation) on trade-by-trade P&L differences.
    Valid at the sample sizes this strategy actually produces (needs a
    proper t-test for very small samples, but n>=30ish is standard
    large-sample territory). Trades pair by list position: option-structure
    and exit-threshold changes affect which trades fire, so the same
    signal-fire dates are re-priced under each candidate.

    Returns (candidate_is_significantly_better, mean_diff, p_value).
    """
    n = len(baseline_pnls)
    if n < MIN_TRADES_FOR_SIGNIFICANCE or n != len(candidate_pnls):
        return False, 0.0, 1.0

    diffs = [c - b for c, b in zip(candidate_pnls, baseline_pnls)]
    mean_diff = sum(diffs) / n
    variance = sum((d - mean_diff) ** 2 for d in diffs) / (n - 1)
    std_diff = variance**0.5

    if std_diff == 0:
        return False, mean_diff, 1.0

    z = mean_diff / (std_diff / (n**0.5))
    p_value = 1 - NormalDist().cdf(z)  # one-sided: is candidate better?
    is_better = p_value < SIGNIFICANCE_LEVEL and mean_diff > 0
    return is_better, mean_diff, p_value


def run_retune():
    current_params = load_params()

    print(f"Fetching {BACKTEST_LOOKBACK_DAYS} days of history for re-tune...")
    bars_by_ticker = fetch_bars(WATCHLIST, lookback_days=BACKTEST_LOOKBACK_DAYS)

    _, baseline_trades, _ = run_backtest(bars_by_ticker=bars_by_ticker, **current_params)
    baseline_pnls = [t.pnl for t in baseline_trades]
    baseline_total = sum(baseline_pnls)

    print(f"Baseline: {len(baseline_trades)} trades, total P&L ${baseline_total:,.2f}")

    if len(baseline_trades) < MIN_TRADES_FOR_SIGNIFICANCE:
        reasoning = (
            f"Only {len(baseline_trades)} historical trades, below the "
            f"{MIN_TRADES_FOR_SIGNIFICANCE}-trade minimum for any comparison "
            f"to be trustworthy. No changes considered."
        )
        print(reasoning)
        log_entry("retune_check", {"changed": False, "reasoning": reasoning})
        return

    best = None  # (param_key, candidate_value, mean_diff, p_value)

    single_value_params = {
        k: v for k, v in CANDIDATE_STEPS.items() if k not in ("min_days_to_expiry",)
    }
    for param_key, candidates in single_value_params.items():
        for candidate_value in candidates:
            trial_params = dict(current_params)
            trial_params[param_key] = candidate_value
            _, trial_trades, _ = run_backtest(bars_by_ticker=bars_by_ticker, **trial_params)
            trial_pnls = [t.pnl for t in trial_trades]

            is_better, mean_diff, p_value = _paired_significance(baseline_pnls, trial_pnls)
            print(f"  {param_key}={candidate_value}: mean diff/trade "
                  f"${mean_diff:+.2f}, p={p_value:.3f}"
                  f"{' (significant)' if is_better else ''}")

            if is_better and (best is None or mean_diff > best[2]):
                best = (param_key, candidate_value, mean_diff, p_value)

    for min_d, max_d in EXPIRY_WINDOW_CANDIDATES:
        trial_params = dict(current_params)
        trial_params["min_days_to_expiry"] = min_d
        trial_params["max_days_to_expiry"] = max_d
        _, trial_trades, _ = run_backtest(bars_by_ticker=bars_by_ticker, **trial_params)
        trial_pnls = [t.pnl for t in trial_trades]

        is_better, mean_diff, p_value = _paired_significance(baseline_pnls, trial_pnls)
        print(f"  expiry_window=({min_d},{max_d}): mean diff/trade "
              f"${mean_diff:+.2f}, p={p_value:.3f}"
              f"{' (significant)' if is_better else ''}")

        if is_better and (best is None or mean_diff > best[2]):
            best = ("expiry_window", (min_d, max_d), mean_diff, p_value)

    if best is None:
        reasoning = (
            f"Tested variations of every tunable parameter against the "
            f"current baseline ({len(baseline_trades)} trades). None "
            f"cleared the {SIGNIFICANCE_LEVEL:.0%} significance bar. "
            f"Keeping current parameters unchanged."
        )
        print(reasoning)
        log_entry("retune_check", {"changed": False, "reasoning": reasoning})
        return

    param_key, candidate_value, mean_diff, p_value = best
    new_params = dict(current_params)
    if param_key == "expiry_window":
        old_value = (current_params["min_days_to_expiry"], current_params["max_days_to_expiry"])
        new_params["min_days_to_expiry"], new_params["max_days_to_expiry"] = candidate_value
    else:
        old_value = current_params[param_key]
        new_params[param_key] = candidate_value

    reasoning = (
        f"{param_key} changed from {old_value} to {candidate_value}: backtested "
        f"${mean_diff:+.2f}/trade better than the current value across "
        f"{len(baseline_trades)} historical trades (p={p_value:.3f}, "
        f"below the {SIGNIFICANCE_LEVEL:.0%} significance bar). Adopting it."
    )
    print(reasoning)
    log_entry(
        "retune_decision",
        {
            "changed": True,
            "param": param_key,
            "old_value": old_value,
            "new_value": candidate_value,
            "mean_pnl_diff_per_trade": mean_diff,
            "p_value": p_value,
            "reasoning": reasoning,
        },
    )
    save_params(new_params)


if __name__ == "__main__":
    run_retune()
