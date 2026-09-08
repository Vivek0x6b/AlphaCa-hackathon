"""
Market-wide regime filter, separate from any single ticker's own signal.

The breakout signal in signals.py only ever looks at one ticker in
isolation - it has no idea whether the broader market is trending up or
getting sold off. A momentum breakout during a broad market downturn is
more likely to be a bull trap than genuine participation. This is a gate
applied on top of an already-fired signal, same shape as the news veto:
it can only block a trade the deterministic signal already generated,
never originate one.

Deterministic and backtestable, unlike the news veto - there's no LLM or
live data feed involved, just SPY's own price history.
"""

import pandas as pd

MARKET_REGIME_MA_DAYS = 200
REGIME_TICKER = "SPY"


def market_regime_ok(regime_bars: pd.DataFrame, ma_days: int = MARKET_REGIME_MA_DAYS) -> bool:
    """
    True if the broader market (SPY) is above its own long moving
    average - i.e. calls are allowed. False means the market itself is
    in a downtrend, so a single ticker's upside breakout is more likely
    to be a false signal.

    Returns True (doesn't filter) if there isn't enough history yet to
    compute the moving average, rather than blocking every trade during
    the warmup period.
    """
    if len(regime_bars) < ma_days:
        return True

    ma = float(regime_bars["close"].tail(ma_days).mean())
    current = float(regime_bars["close"].iloc[-1])
    return current > ma
