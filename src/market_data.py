"""
Market data access via Alpaca's REST API (alpaca-py), used directly for
standalone testing/running of the agent loop (see CLAUDE_CODE_CONTEXT.md
for why this is separate from the MCP/Hermes integration path).
"""

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestTradeRequest,
    OptionChainRequest,
    OptionLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import Adjustment
from alpaca.trading.enums import ContractType

from config.watchlist import MIN_DAYS_TO_EXPIRY, MAX_DAYS_TO_EXPIRY

# Credentials live outside the repo on purpose. Never committed.
ENV_PATH = Path(r"C:\Users\vivek\Desktop\alpaca.env")

# How far back to ask for daily bars. signals.py needs at least 51 trading
# days of history. 150 calendar days gives comfortable room for weekends
# and market holidays.
DEFAULT_LOOKBACK_DAYS = 150


def load_credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH)
    return os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]


def get_data_client() -> StockHistoricalDataClient:
    """Load Alpaca credentials and construct the stock market data client."""
    api_key, secret_key = load_credentials()
    return StockHistoricalDataClient(api_key, secret_key)


def get_option_data_client() -> OptionHistoricalDataClient:
    """Load Alpaca credentials and construct the option market data client."""
    api_key, secret_key = load_credentials()
    return OptionHistoricalDataClient(api_key, secret_key)


def fetch_bars(
    tickers: list[str],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, pd.DataFrame]:
    """
    Fetch daily OHLCV bars for a list of tickers, shaped for signals.py.

    Returns a dict of {ticker: DataFrame}, each DataFrame with columns
    ['open', 'high', 'low', 'close', 'volume'], oldest bar first.
    A ticker with no bars in the response (for example if Alpaca has no
    data for it) is simply left out of the returned dict.
    """
    client = get_data_client()

    request = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=lookback_days),
        adjustment=Adjustment.SPLIT,
    )
    barset = client.get_stock_bars(request)
    all_bars = barset.df

    bars_by_ticker = {}
    for ticker in tickers:
        if ticker not in all_bars.index.get_level_values("symbol"):
            continue
        bars_by_ticker[ticker] = all_bars.loc[ticker]

    return bars_by_ticker


def _parse_occ_symbol(symbol: str) -> tuple[date, Literal["call", "put"], float]:
    """
    Break an OCC option symbol into (expiration_date, option_type, strike).

    Format is fixed: root symbol, then YYMMDD, then C or P, then an
    8-digit strike price (the strike times 1000). Example:
    "SPY260911C00717000" means SPY, expires 2026-09-11, call, strike 717.0.
    """
    tail = symbol[-15:]
    expiry = datetime.strptime(tail[:6], "%y%m%d").date()
    option_type = "call" if tail[6] == "C" else "put"
    strike = int(tail[7:]) / 1000
    return expiry, option_type, strike


def fetch_option_chain(
    ticker: str,
    direction: Literal["call", "put"],
    min_days_to_expiry: int = MIN_DAYS_TO_EXPIRY,
    max_days_to_expiry: int = MAX_DAYS_TO_EXPIRY,
) -> list[dict]:
    """
    Fetch an option chain for one ticker, shaped for options_selector.py.

    Only asks Alpaca for calls or puts (matching direction), and only for
    expirations inside the strategy's expiry window. Defaults to
    config/watchlist.py; overridable so the expiry window can come from
    the mutable strategy_params.py file instead, once the autonomous
    re-tuner is adjusting it. Contracts with no delta available (illiquid,
    no recent trade) are left out, since options_selector.py can't filter
    by delta without one.

    Returns a list of dicts with: symbol, strike_price, expiration_date
    (ISO string), delta, option_type.
    """
    client = get_option_data_client()

    request = OptionChainRequest(
        underlying_symbol=ticker,
        type=ContractType.CALL if direction == "call" else ContractType.PUT,
        expiration_date_gte=date.today() + timedelta(days=min_days_to_expiry),
        expiration_date_lte=date.today() + timedelta(days=max_days_to_expiry),
    )
    chain = client.get_option_chain(request)

    contracts = []
    for symbol, snapshot in chain.items():
        if snapshot.greeks is None or snapshot.greeks.delta is None:
            continue
        expiry, option_type, strike = _parse_occ_symbol(symbol)
        contracts.append(
            {
                "symbol": symbol,
                "strike_price": strike,
                "expiration_date": expiry.isoformat(),
                "delta": snapshot.greeks.delta,
                "option_type": option_type,
            }
        )

    return contracts


def fetch_stock_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch the current (latest trade) price for a list of stock tickers."""
    client = get_data_client()

    request = StockLatestTradeRequest(symbol_or_symbols=tickers)
    trades = client.get_stock_latest_trade(request)

    return {ticker: trade.price for ticker, trade in trades.items()}


def fetch_option_quotes(symbols: list[str]) -> dict[str, tuple[float, float]]:
    """
    Fetch the current bid/ask for a list of option symbols.

    Returns a dict of {symbol: (bid_price, ask_price)}.
    """
    client = get_option_data_client()

    request = OptionLatestQuoteRequest(symbol_or_symbols=symbols)
    quotes = client.get_option_latest_quote(request)

    return {
        symbol: (quote.bid_price, quote.ask_price)
        for symbol, quote in quotes.items()
    }
