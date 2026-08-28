"""
Breakout signal detection.

For each ticker: check for a breakout above/below the N-day high/low,
confirm it aligns with the trend (price vs. moving average), and require
volume confirmation before flagging a signal.

This module is pure logic. It takes bar data in and returns a decision
with reasoning out. It does not place orders or call Alpaca directly;
that keeps it independently testable and reusable for backtesting.
"""

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from config.watchlist import (
    BREAKOUT_LOOKBACK_DAYS,
    TREND_MA_DAYS,
    RELATIVE_VOLUME_LOOKBACK_DAYS,
    RELATIVE_VOLUME_MULTIPLIER,
)


@dataclass
class SignalResult:
    ticker: str
    fired: bool
    direction: Literal["call", "put", None]
    reasoning: str
    # Raw values, useful for logging/journaling and backtesting review.
    close: float | None = None
    breakout_level: float | None = None
    trend_ma: float | None = None
    relative_volume: float | None = None


def evaluate_signal(ticker: str, bars: pd.DataFrame) -> SignalResult:
    """
    Evaluate breakout + trend + volume conditions for a single ticker.

    Parameters
    ----------
    ticker: str
        Ticker symbol, used for logging only.
    bars: pd.DataFrame
        Daily OHLCV bars, oldest first, with columns:
        ['open', 'high', 'low', 'close', 'volume']. Needs at least
        max(BREAKOUT_LOOKBACK_DAYS, TREND_MA_DAYS) + 1 rows.

    Returns
    -------
    SignalResult
    """
    min_rows = max(BREAKOUT_LOOKBACK_DAYS, TREND_MA_DAYS) + 1
    if len(bars) < min_rows:
        return SignalResult(
            ticker=ticker,
            fired=False,
            direction=None,
            reasoning=f"Not enough history ({len(bars)} rows, need {min_rows}).",
        )

    latest = bars.iloc[-1]
    prior = bars.iloc[:-1]

    close = float(latest["close"])
    volume = float(latest["volume"])

    # Breakout levels computed from data BEFORE today's bar, so today's
    # close is being tested against yesterday's-and-earlier range.
    lookback = prior.tail(BREAKOUT_LOOKBACK_DAYS)
    breakout_high = float(lookback["high"].max())
    breakout_low = float(lookback["low"].min())

    trend_ma = float(prior["close"].tail(TREND_MA_DAYS).mean())

    avg_volume = float(prior["volume"].tail(RELATIVE_VOLUME_LOOKBACK_DAYS).mean())
    relative_volume = volume / avg_volume if avg_volume else 0.0

    volume_confirmed = relative_volume >= RELATIVE_VOLUME_MULTIPLIER

    # --- Call setup: breakout above prior high, price above trend MA ---
    if close > breakout_high and close > trend_ma:
        if volume_confirmed:
            return SignalResult(
                ticker=ticker,
                fired=True,
                direction="call",
                reasoning=(
                    f"{ticker} closed at {close:.2f}, above the "
                    f"{BREAKOUT_LOOKBACK_DAYS}-day high of {breakout_high:.2f}, "
                    f"and above the {TREND_MA_DAYS}-day MA of {trend_ma:.2f}. "
                    f"Volume was {relative_volume:.2f}x the "
                    f"{RELATIVE_VOLUME_LOOKBACK_DAYS}-day average, confirming "
                    f"the move."
                ),
                close=close,
                breakout_level=breakout_high,
                trend_ma=trend_ma,
                relative_volume=relative_volume,
            )
        return SignalResult(
            ticker=ticker,
            fired=False,
            direction=None,
            reasoning=(
                f"{ticker} broke above the {BREAKOUT_LOOKBACK_DAYS}-day high "
                f"and is above trend, but volume was only "
                f"{relative_volume:.2f}x average (need "
                f"{RELATIVE_VOLUME_MULTIPLIER}x). No real participation, "
                f"skipping."
            ),
            close=close,
            breakout_level=breakout_high,
            trend_ma=trend_ma,
            relative_volume=relative_volume,
        )

    # --- Put setup: breakdown below prior low, price below trend MA ---
    if close < breakout_low and close < trend_ma:
        if volume_confirmed:
            return SignalResult(
                ticker=ticker,
                fired=True,
                direction="put",
                reasoning=(
                    f"{ticker} closed at {close:.2f}, below the "
                    f"{BREAKOUT_LOOKBACK_DAYS}-day low of {breakout_low:.2f}, "
                    f"and below the {TREND_MA_DAYS}-day MA of {trend_ma:.2f}. "
                    f"Volume was {relative_volume:.2f}x the "
                    f"{RELATIVE_VOLUME_LOOKBACK_DAYS}-day average, confirming "
                    f"the move."
                ),
                close=close,
                breakout_level=breakout_low,
                trend_ma=trend_ma,
                relative_volume=relative_volume,
            )
        return SignalResult(
            ticker=ticker,
            fired=False,
            direction=None,
            reasoning=(
                f"{ticker} broke below the {BREAKOUT_LOOKBACK_DAYS}-day low "
                f"and is below trend, but volume was only "
                f"{relative_volume:.2f}x average (need "
                f"{RELATIVE_VOLUME_MULTIPLIER}x). No real participation, "
                f"skipping."
            ),
            close=close,
            breakout_level=breakout_low,
            trend_ma=trend_ma,
            relative_volume=relative_volume,
        )

    return SignalResult(
        ticker=ticker,
        fired=False,
        direction=None,
        reasoning=(
            f"{ticker} closed at {close:.2f}, inside the "
            f"{BREAKOUT_LOOKBACK_DAYS}-day range "
            f"({breakout_low:.2f}-{breakout_high:.2f}). No breakout."
        ),
        close=close,
        trend_ma=trend_ma,
        relative_volume=relative_volume,
    )


def scan_watchlist(bars_by_ticker: dict[str, pd.DataFrame]) -> list[SignalResult]:
    """Evaluate every ticker in a {ticker: bars} mapping."""
    return [evaluate_signal(ticker, bars) for ticker, bars in bars_by_ticker.items()]
