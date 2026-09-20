"""
Composite entry signal: combines several IC-scored factors (src/factors.py)
into one score, instead of signals.py's single binary breakout condition.

Fires a call signal when the weighted, z-scored combination of factors
crosses a threshold AND price is above its long-term trend (kept from the
existing strategy as a sanity filter, not from the factor combination
itself). Long-only, matching the rest of this project's calls-only design
(PUT_TRADING_ENABLED = False, backed by its own separate evidence in
config/watchlist.py).

Weights come from scripts/factor_research.py's IC output via
weights_from_ic() - a one-time (or occasional) research step, not
something recomputed live. This keeps the actual entry decision fully
deterministic and backtestable: no LLM involved, same as signals.py.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.factors import FACTORS
from src.signals import SignalResult

Z_SCORE_WINDOW = 100  # trailing window used to normalize each factor's scale


def weights_from_ic(ic_by_factor: dict[str, float]) -> dict[str, float]:
    """
    Signed, magnitude-normalized weights from raw IC values (from
    scripts/factor_research.py). A factor with IC=+0.16 gets a large
    positive weight (higher factor value -> higher composite score);
    IC=-0.04 gets a small negative weight (higher factor value -> LOWER
    composite score, since it's historically predicted worse forward
    returns). Weak factors (near-zero IC) naturally end up with near-zero
    weight - no separate cutoff needed.
    """
    valid = {name: ic for name, ic in ic_by_factor.items() if ic is not None}
    total_abs = sum(abs(ic) for ic in valid.values())
    if total_abs == 0:
        return {name: 0.0 for name in valid}
    return {name: ic / total_abs for name, ic in valid.items()}


def _compute_composite_score(bars: pd.DataFrame, weights: dict[str, float]) -> float | None:
    """
    Today's composite score: each factor's current value, z-scored
    against its own trailing history (so factors on very different scales
    - momentum ~0.05, RSI ~50 - combine meaningfully), then combined by
    the given weights. None if there isn't enough history for every
    weighted factor yet.
    """
    total = 0.0
    any_contributed = False

    for name, weight in weights.items():
        if weight == 0 or name not in FACTORS:
            continue
        factor_fn, min_rows = FACTORS[name]
        if len(bars) < min_rows + Z_SCORE_WINDOW:
            return None

        series = factor_fn(bars).dropna()
        if len(series) < Z_SCORE_WINDOW + 1:
            return None

        current = series.iloc[-1]
        history = series.iloc[-(Z_SCORE_WINDOW + 1):-1]
        mean, std = history.mean(), history.std()
        if std == 0 or pd.isna(std):
            continue

        z = (current - mean) / std
        total += weight * z
        any_contributed = True

    return total if any_contributed else None


def evaluate_composite_signal(
    ticker: str,
    bars: pd.DataFrame,
    weights: dict[str, float],
    threshold: float = 1.0,
    trend_ma_days: int = 50,
) -> SignalResult:
    """
    Fires a call signal when the composite score clears `threshold`
    (in z-score units - 1.0 means "about 1 standard deviation above this
    factor combination's own recent normal") and price is above its
    trend_ma_days moving average. The moving average also becomes the
    signal's breakout_level for downstream exit logic: the trade's thesis
    is "price stays above its trend," so a fall back below that level
    invalidates it, exactly the same semantics as the existing breakout
    signal's thesis-invalidation rule.
    """
    min_rows = trend_ma_days + Z_SCORE_WINDOW + 1
    if len(bars) < min_rows:
        return SignalResult(
            ticker=ticker, fired=False, direction=None,
            reasoning=f"Not enough history ({len(bars)} rows, need {min_rows}).",
        )

    score = _compute_composite_score(bars, weights)
    close = float(bars["close"].iloc[-1])
    trend_ma = float(bars["close"].tail(trend_ma_days).mean())

    if score is None:
        return SignalResult(
            ticker=ticker, fired=False, direction=None,
            reasoning=f"{ticker}: composite score unavailable (insufficient factor history).",
            close=close, trend_ma=trend_ma,
        )

    fired = score >= threshold and close > trend_ma

    if fired:
        return SignalResult(
            ticker=ticker, fired=True, direction="call",
            reasoning=(
                f"{ticker} composite factor score is {score:+.2f} "
                f"(>= {threshold:+.2f} threshold) and price ({close:.2f}) is "
                f"above its {trend_ma_days}-day trend ({trend_ma:.2f})."
            ),
            close=close, breakout_level=trend_ma, trend_ma=trend_ma,
        )

    return SignalResult(
        ticker=ticker, fired=False, direction=None,
        reasoning=(
            f"{ticker} composite factor score is {score:+.2f} "
            f"(threshold {threshold:+.2f}), price {'above' if close > trend_ma else 'below'} "
            f"trend. No signal."
        ),
        close=close, trend_ma=trend_ma,
    )
