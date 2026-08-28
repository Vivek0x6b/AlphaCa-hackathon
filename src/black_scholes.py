"""
Black-Scholes option pricing, used only for backtesting.

Alpaca has no historical option chain data (no way to know what strikes or
deltas were actually available on a past date), so the backtester estimates
option prices and deltas itself from real historical stock prices plus a
volatility estimate. This is an approximation, not real market data: see
docs/designs/autonomous-backtest-retuning.md for the tradeoffs.
"""

import math
from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd

# Trailing window for the realized-volatility IV proxy. Real market IV
# usually runs a bit above realized vol (the volatility risk premium), so
# using realized vol tends to underprice options slightly, which makes a
# backtest look somewhat better than live trading would. See
# docs/designs/autonomous-backtest-retuning.md for the full tradeoff.
REALIZED_VOL_WINDOW_DAYS = 20

# Constant risk-free rate assumption. Dividends are not modeled: their effect
# on short-dated (2-4 week) equity option pricing is small relative to the
# approximation error already introduced by not having real historical IV.
RISK_FREE_RATE = 0.045


def _norm_cdf(x: float) -> float:
    """Standard normal CDF, via the stdlib error function (no scipy needed)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(
    spot: float,
    strike: float,
    years_to_expiry: float,
    volatility: float,
    option_type: Literal["call", "put"],
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """Black-Scholes theoretical price for a European call or put."""
    if years_to_expiry <= 0 or volatility <= 0:
        # At/after expiry, or degenerate volatility: price is just intrinsic value.
        if option_type == "call":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)

    d1 = (
        math.log(spot / strike) + (risk_free_rate + volatility**2 / 2) * years_to_expiry
    ) / (volatility * math.sqrt(years_to_expiry))
    d2 = d1 - volatility * math.sqrt(years_to_expiry)

    discount = math.exp(-risk_free_rate * years_to_expiry)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(
    spot: float,
    strike: float,
    years_to_expiry: float,
    volatility: float,
    option_type: Literal["call", "put"],
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """Black-Scholes delta for a European call or put."""
    if years_to_expiry <= 0 or volatility <= 0:
        # At/after expiry: delta is 1/0 (call) or -1/0 (put) depending on moneyness.
        if option_type == "call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0

    d1 = (
        math.log(spot / strike) + (risk_free_rate + volatility**2 / 2) * years_to_expiry
    ) / (volatility * math.sqrt(years_to_expiry))

    if option_type == "call":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def compute_realized_volatility(
    closes: pd.Series, window: int = REALIZED_VOL_WINDOW_DAYS
) -> pd.Series:
    """
    Trailing annualized realized volatility, used as the backtester's IV
    proxy since there's no historical options IV data available.

    Only ever uses data up to and including each row's own date (a rolling
    window looking backward), so this never leaks future information into
    a backtest.
    """
    log_returns = np.log(closes / closes.shift(1))
    return log_returns.rolling(window).std() * math.sqrt(252)


def synthesize_option_chain(
    underlying_price: float,
    as_of_date: date,
    direction: Literal["call", "put"],
    volatility: float,
    min_days_to_expiry: int,
    max_days_to_expiry: int,
) -> list[dict]:
    """
    Build a fake-but-correctly-shaped option chain for backtesting.

    Shaped exactly like market_data.fetch_option_chain()'s real output
    (symbol, strike_price, expiration_date, delta, option_type), so it can
    be handed straight to options_selector.select_debit_spread() unchanged.
    Adds one extra field, "price" (the Black-Scholes theoretical price),
    which the backtester needs to compute the spread's net debit.

    Generates a few candidate expiries across the window (weekly steps) and
    a strike grid spanning +/-30% of the underlying price, which is enough
    range to cover realistic delta targets without needing real listed
    strike increments.
    """
    chain = []
    for days_out in range(min_days_to_expiry, max_days_to_expiry + 1, 7):
        expiry = as_of_date + timedelta(days=days_out)
        years_to_expiry = days_out / 365.25

        strike_step = max(0.5, round(underlying_price * 0.005, 2))
        strike = round(underlying_price * 0.7, 2)
        high = underlying_price * 1.3
        while strike <= high:
            delta = bs_delta(underlying_price, strike, years_to_expiry, volatility, direction)
            price = bs_price(underlying_price, strike, years_to_expiry, volatility, direction)
            chain.append(
                {
                    "symbol": f"SYN{expiry.isoformat()}{direction[0].upper()}{strike:.2f}",
                    "strike_price": strike,
                    "expiration_date": expiry.isoformat(),
                    "delta": delta,
                    "option_type": direction,
                    "price": price,
                }
            )
            strike = round(strike + strike_step, 2)

    return chain
