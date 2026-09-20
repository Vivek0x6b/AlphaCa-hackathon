"""
Market-wide volatility-regime position sizing, adapted from "Sizing the
Risk: Kelly, VIX, and Hybrid Approaches in Put-Writing on Index Options"
(arXiv:2508.16598) and "Construction and Hedging of Equity Index Options
Portfolios" (arXiv:2407.13908) - both use VIX percentile to scale size
down in high-volatility regimes and up in calm ones.

Alpaca has no VIX data, so SPY's own realized volatility percentile
(relative to its own trailing history) is used as a market-wide proxy -
the same kind of substitution already used and documented throughout this
project's backtest (realized vol standing in for implied vol).

Deliberately different from every other volatility-based idea already
tried and rejected in this project (using a TICKER's own volatility as an
entry driver, an exit-threshold scaler, or an entry veto - all three
failed): this uses the MARKET's volatility, not any individual ticker's,
and only to scale position size, continuously - not to gate entries or
exits at all. Built as a smooth linear function of percentile rank
specifically to avoid the cliff-edge fragility a hard threshold can have
(see docs/strategy-scorecard.md's entry-cap rejection).
"""

import pandas as pd

VOL_WINDOW = 20  # realized vol computed over this many days
PERCENTILE_WINDOW = 252  # ~1 trading year of history to rank today's vol against


def compute_market_vol_percentile(spy_bars: pd.DataFrame, vol_window: int = VOL_WINDOW, percentile_window: int = PERCENTILE_WINDOW) -> float | None:
    """
    Where today's SPY realized volatility ranks within its own trailing
    year (0.0 = calmest day in the window, 1.0 = most volatile). None if
    there isn't enough history yet.
    """
    returns = spy_bars["close"].pct_change()
    realized_vol = returns.rolling(vol_window).std()

    if len(realized_vol.dropna()) < percentile_window + 1:
        return None

    history = realized_vol.dropna().iloc[-(percentile_window + 1):-1]
    current = realized_vol.dropna().iloc[-1]

    return float((history < current).mean())


def market_vol_size_multiplier(
    vol_percentile: float | None,
    min_multiplier: float = 0.5,
    max_multiplier: float = 1.5,
) -> float:
    """
    Linear, continuous scale from max_multiplier (calmest market) down to
    min_multiplier (most volatile market) - no threshold, no cliff. None
    (not enough history yet) returns 1.0x, i.e. no adjustment.
    """
    if vol_percentile is None:
        return 1.0
    return max_multiplier - (max_multiplier - min_multiplier) * vol_percentile
