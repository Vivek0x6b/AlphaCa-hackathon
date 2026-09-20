"""
Volatility-spike entry veto.

Motivated by "Accounting for Earnings Announcements in the Pricing of
Equity Options" (arXiv:1412.8414) and the broader IV-crush literature:
implied volatility spikes ahead of a known event (most commonly earnings)
and crushes right after it resolves - buying an option into that spike
risks paying inflated premium right before it reverts down, the same
mechanism already confirmed harmful earlier in this project (see
docs/strategy-scorecard.md's volatility-crush finding from the composite
signal experiment).

This project has no historical earnings-calendar data source, and adding
one just to test an idea isn't worth a new external dependency. Instead
this approximates the same real phenomenon with data already on hand:
a stock's very short-term realized volatility spiking well above its own
recent baseline is the same signature an approaching event produces
(elevated uncertainty priced in ahead of a known catalyst), whatever the
specific cause. This is a VETO on an already-fired breakout signal, same
shape as the news veto (src/news_veto.py) - it can only block a trade,
never originate one.
"""

import pandas as pd

SHORT_WINDOW = 5
BASELINE_WINDOW = 60
SPIKE_THRESHOLD = 1.5  # short-term realized vol must exceed this multiple of baseline to veto


def vol_spike_veto(bars: pd.DataFrame, threshold: float = SPIKE_THRESHOLD) -> tuple[bool, str]:
    """
    Returns (vetoed, reasoning). True means skip the trade - recent
    volatility has spiked well above this ticker's own normal baseline,
    the same signature an approaching event (most commonly earnings)
    produces ahead of an IV crush.
    """
    min_rows = BASELINE_WINDOW + 1
    if len(bars) < min_rows:
        return False, "Not enough history to assess a volatility spike."

    returns = bars["close"].pct_change()
    short_vol = returns.tail(SHORT_WINDOW).std()
    baseline_vol = returns.tail(BASELINE_WINDOW).std()

    if pd.isna(short_vol) or pd.isna(baseline_vol) or baseline_vol == 0:
        return False, "Volatility spike check unavailable (insufficient data)."

    ratio = short_vol / baseline_vol
    if ratio >= threshold:
        return True, (
            f"Recent {SHORT_WINDOW}-day volatility is {ratio:.1f}x the "
            f"{BASELINE_WINDOW}-day baseline (>= {threshold:.1f}x) - looks "
            f"like an approaching event (e.g. earnings) rather than a "
            f"clean breakout. Skipping to avoid buying into an IV spike "
            f"right before it crushes."
        )
    return False, f"Recent volatility is {ratio:.1f}x baseline, within normal range."
