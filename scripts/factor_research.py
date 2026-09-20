"""
Compute the Information Coefficient (IC) of each candidate factor in
src/factors.py against real forward returns - the same evaluation
methodology as the paper this was adapted from (arXiv:2409.06289, Table 2),
applied to this project's actual watchlist and history.

IC = Pearson correlation between a factor's value on day t and the
forward return from day t to day t+FORWARD_WINDOW. A higher |IC| means
the factor is more predictive; the sign says which direction (positive =
higher factor value predicts higher forward returns).

This is a one-time (or occasional) research step, not something that
runs live - its output (IC per factor) becomes the fixed weights passed
to src/composite_signal.py, which IS what runs live/in the backtest.
Re-run this if the watchlist or history window changes meaningfully.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.watchlist import WATCHLIST
from src.market_data import fetch_bars
from src.factors import FACTORS

FORWARD_WINDOW = 14  # trading days - matches the strategy's typical hold period
LOOKBACK_DAYS = 730


def compute_ic(bars_by_ticker: dict[str, pd.DataFrame]) -> dict[str, float]:
    """
    Pools factor-value/forward-return pairs across every ticker (same
    "panel" approach as the paper's cross-sectional IC test - one
    correlation per factor across all stocks and dates combined, not a
    separate IC per ticker).
    """
    ic_by_factor = {}

    for factor_name, (factor_fn, min_rows) in FACTORS.items():
        all_factor_values = []
        all_forward_returns = []

        for ticker, df in bars_by_ticker.items():
            if len(df) < min_rows + FORWARD_WINDOW:
                continue

            factor_series = factor_fn(df)
            forward_return = df["close"].shift(-FORWARD_WINDOW) / df["close"] - 1

            paired = pd.DataFrame({"factor": factor_series, "forward_return": forward_return}).dropna()
            all_factor_values.extend(paired["factor"].tolist())
            all_forward_returns.extend(paired["forward_return"].tolist())

        if len(all_factor_values) < 30:
            ic_by_factor[factor_name] = None
            continue

        ic = pd.Series(all_factor_values).corr(pd.Series(all_forward_returns))
        ic_by_factor[factor_name] = ic

    return ic_by_factor


def print_ic_table(ic_by_factor: dict[str, float]):
    print(f"\n{'Factor':<25} {'IC':>10} {'|IC| rank'}")
    print("-" * 50)
    ranked = sorted(
        [(name, ic) for name, ic in ic_by_factor.items() if ic is not None],
        key=lambda x: abs(x[1]),
        reverse=True,
    )
    for rank, (name, ic) in enumerate(ranked, 1):
        print(f"{name:<25} {ic:>+10.4f} #{rank}")

    skipped = [name for name, ic in ic_by_factor.items() if ic is None]
    if skipped:
        print(f"\nSkipped (not enough data): {skipped}")


if __name__ == "__main__":
    print(f"Fetching {LOOKBACK_DAYS} days of history for {len(WATCHLIST)} tickers...")
    bars_by_ticker = fetch_bars(WATCHLIST, lookback_days=LOOKBACK_DAYS)

    ic_by_factor = compute_ic(bars_by_ticker)
    print_ic_table(ic_by_factor)
