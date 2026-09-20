"""
Alpha factor library - candidate signals scored by Information Coefficient
(IC) and combined into one composite entry score, instead of the single
binary breakout condition in signals.py.

Adapts the evaluation methodology from "Automate Strategy Finding with LLM
in Quant Investment" (arXiv:2409.06289) - Information Coefficient scoring,
per-category factors, weighted combination - without the paper's LLM-
generates-the-factors step. That step has no ablation evidence behind it
in the paper itself (their ablation study only tests removing the two
evaluation agents, never "human-picked factors" as a baseline), and it
would mean an LLM inventing and executing arbitrary math formulas against
real trades - a real reversal of this project's "LLM narrates or vetoes,
never originates a trade" boundary, for an unproven benefit. These factors
are instead pulled from well-established, publicly documented quant
literature (momentum, RSI, realized volatility, relative volume, trend
distance, Bollinger position) - the same breadth of candidates the paper's
LLM step produced, authored by a human instead.

Every function is vectorized and strictly causal: each row's value only
depends on that row and earlier ones (rolling/shift-based), so a factor
computed "as of day t" has no knowledge of day t+1 onward - required for
the IC computation (src/factor_research.py) to be a valid predictive-power
test, not a leak.
"""

import numpy as np
import pandas as pd


def momentum(df: pd.DataFrame, window: int) -> pd.Series:
    return df["close"].pct_change(window)


def rsi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def realized_volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
    return df["close"].pct_change().rolling(window).std()


def relative_volume(df: pd.DataFrame, window: int = 20) -> pd.Series:
    # shift(1) so today's volume is compared against the average BEFORE
    # today - matches signals.py's existing relative-volume convention.
    avg_volume = df["volume"].shift(1).rolling(window).mean()
    return df["volume"] / avg_volume.replace(0, np.nan)


def trend_distance(df: pd.DataFrame, window: int = 50) -> pd.Series:
    ma = df["close"].rolling(window).mean()
    return (df["close"] - ma) / ma


def bollinger_position(df: pd.DataFrame, window: int = 20, num_std: float = 2.0) -> pd.Series:
    ma = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    return (df["close"] - lower) / (upper - lower).replace(0, np.nan)


# name -> (function, min rows needed before it produces a real value).
# Mirrors the paper's category spread (Momentum, Mean Reversion via
# Bollinger position, Volatility, Volume) minus Fundamental/Growth - no
# fundamentals data source exists in this project.
FACTORS: dict[str, tuple] = {
    "momentum_20": (lambda df: momentum(df, 20), 21),
    "momentum_60": (lambda df: momentum(df, 60), 61),
    "rsi_14": (lambda df: rsi(df, 14), 15),
    "volatility_20": (lambda df: realized_volatility(df, 20), 21),
    "relative_volume_20": (lambda df: relative_volume(df, 20), 22),
    "trend_distance_50": (lambda df: trend_distance(df, 50), 51),
    "bollinger_position_20": (lambda df: bollinger_position(df, 20), 21),
}
