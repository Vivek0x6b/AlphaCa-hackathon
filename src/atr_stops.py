"""
ATR-based adaptive profit-target/stop-loss thresholds.

The live strategy uses one fixed profit target (+50%) and stop loss (-40%)
of the option debit paid, the same for every ticker regardless of how
volatile that name actually is. Adapted from the widely-used ATR
(Average True Range) approach to sizing stops/targets to a security's own
recent volatility (see e.g. arXiv:2604.27150, "Optimal Stop-Loss and
Take-Profit Parameterization") - a quiet stock gets a tighter threshold, a
wild one gets more room, instead of one-size-fits-all.

Kept as a per-ticker SCALING of the already-backtested fixed thresholds,
not a wholesale replacement: the 50%/40% baseline is preserved as the
"typical volatility" case, and each ticker's threshold flexes up or down
from there in proportion to its own ATR relative to the watchlist's
typical ATR. This is fully deterministic, no LLM involved - same
philosophy as the composite factor signal experiment (docs/strategy-
scorecard.md), just applied to exits instead of entries.
"""

import pandas as pd

ATR_WINDOW = 14

# How far the adaptive threshold is allowed to move from the fixed
# baseline in either direction - without this, one ticker having an
# unusually calm or wild recent stretch could produce an absurdly tight
# or wide threshold that was never actually backtested.
MIN_SCALE = 0.5
MAX_SCALE = 2.0


def compute_atr(bars: pd.DataFrame, window: int = ATR_WINDOW) -> pd.Series:
    """Wilder's Average True Range, as a fraction of price (so it's
    comparable across tickers at very different price levels)."""
    prev_close = bars["close"].shift(1)
    true_range = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.rolling(window).mean()
    return atr / bars["close"]


def compute_baseline_atr_pct(bars_by_ticker: dict[str, pd.DataFrame], window: int = ATR_WINDOW) -> float:
    """
    The watchlist's typical (median) ATR%, across every ticker and every
    day with enough history. This is the "typical volatility" the
    existing fixed 50%/40% thresholds were implicitly tuned against -
    a ticker at this exact ATR% gets no adjustment; one above or below it
    scales proportionally.
    """
    all_values = []
    for df in bars_by_ticker.values():
        atr_pct = compute_atr(df, window).dropna()
        all_values.extend(atr_pct.tolist())
    return float(pd.Series(all_values).median())


def adaptive_thresholds(
    atr_pct_at_entry: float,
    baseline_atr_pct: float,
    base_profit_target_pct: float,
    base_stop_loss_pct: float,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
) -> tuple[float, float]:
    """
    Scale the fixed profit-target/stop-loss thresholds by how volatile
    this ticker is right now relative to the watchlist's typical
    volatility, clipped to [MIN_SCALE, MAX_SCALE]x the baseline so one
    ticker's unusual recent volatility can't produce an untested extreme.
    """
    if baseline_atr_pct <= 0:
        return base_profit_target_pct, base_stop_loss_pct

    scale = atr_pct_at_entry / baseline_atr_pct
    scale = max(min_scale, min(max_scale, scale))

    return base_profit_target_pct * scale, base_stop_loss_pct * scale
