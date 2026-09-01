"""
Watchlist and signal parameters for AlphaCa.

Adjust these values to tune the strategy. Keep the reasoning behind each
choice in comments here so the thesis stays traceable.
"""

# Liquid, optionable large-caps and ETFs.
#
# Widened from the original 7 to 13 on 2026-09-01. The backtest showed
# roughly one signal every 13 trading days across the original watchlist,
# which is less than 50/50 odds of anything firing at all during a single
# ~5-day judging week. This adds more names of the same quality bar
# (mega-cap, heavily optioned, liquid) rather than loosening the signal
# logic itself - same validated rules, more opportunities to apply them
# to, not a different or looser thesis.
WATCHLIST = [
    "SPY",
    "QQQ",
    "AAPL",
    "NVDA",
    "MSFT",
    "AMD",
    "TSLA",
    "GOOGL",
    "META",
    "AMZN",
    "NFLX",
    "IWM",
    "DIA",
]

# --- Signal parameters ---

# Breakout lookback window (days). A close above the N-day high (for calls)
# or below the N-day low (for puts) is the breakout condition.
BREAKOUT_LOOKBACK_DAYS = 20

# Trend filter. Only take calls when price is above this moving average,
# only take puts when price is below it. Keeps trades aligned with trend.
TREND_MA_DAYS = 50

# Relative volume threshold. Breakout day volume must be at least this
# multiple of the trailing average volume to confirm real participation.
RELATIVE_VOLUME_LOOKBACK_DAYS = 20
RELATIVE_VOLUME_MULTIPLIER = 1.5

# --- Options selection parameters ---

# Target delta ranges for the debit spread legs.
LONG_LEG_DELTA_RANGE = (0.40, 0.50)

# Short leg lowered from (0.15, 0.20) after backtesting (see
# docs/designs/autonomous-backtest-retuning.md): a further out-of-the-money
# short leg widens the spread, giving more room to profit if the move
# continues. Improved the calls-only backtest from +3.09% to +4.07% over
# the same 2-year window and 39 trades; a swept delta/expiry range of
# alternatives around this one all underperformed it.
SHORT_LEG_DELTA_RANGE = (0.10, 0.15)

# Expiration window (calendar days out).
MIN_DAYS_TO_EXPIRY = 14
MAX_DAYS_TO_EXPIRY = 28

# --- Risk parameters ---

# Fraction of account equity risked per trade.
POSITION_SIZE_PCT = 0.02

# Maximum number of concurrent open positions.
MAX_CONCURRENT_POSITIONS = 3

# --- Direction ---

# Whether put (bearish breakdown) trades are actually taken. Backtesting
# (see docs/designs/autonomous-backtest-retuning.md) found puts
# underperformed calls consistently across 5 of 7 watchlist tickers over a
# 2-year window (avg forward return -4.36% vs +2.64% for calls), and a
# calls-only backtest was profitable (+3.09%) where the bidirectional
# version was not. Put signals still fire and get journaled (signals.py is
# unchanged), they're just not traded while this is False.
PUT_TRADING_ENABLED = False
