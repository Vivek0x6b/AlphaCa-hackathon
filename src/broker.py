"""
Account and position access via Alpaca's trading client.

Kept separate from market_data.py on purpose: the trading client is the
one that can also place and cancel real orders, while market_data.py only
ever reads prices. Keeping them apart makes it obvious at a glance which
code just looks at data and which code touches the account.
"""

import time

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetClass,
    OrderClass,
    OrderSide,
    PositionIntent,
    PositionSide,
    TimeInForce,
)
from alpaca.trading.models import Order, Position
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

from src.execution import OrderPlan
from src.market_data import load_credentials, fetch_option_quotes


def get_trading_client() -> TradingClient:
    """Load Alpaca credentials and construct the trading client."""
    api_key, secret_key = load_credentials()
    return TradingClient(api_key, secret_key, paper=True)


def get_account_equity() -> float:
    """Current total account equity, used to size positions."""
    client = get_trading_client()
    account = client.get_account()
    return float(account.equity)


def get_open_spread_count() -> int:
    """
    Number of tickers with an open option position.

    One debit spread shows up as two positions in the account (the long
    leg and the short leg), so counting raw positions would double-count
    each open spread. Counting distinct underlying tickers instead gives
    the number of open spread trades, which is what MAX_CONCURRENT_POSITIONS
    is meant to limit.
    """
    client = get_trading_client()
    positions = client.get_all_positions()

    option_positions = [p for p in positions if p.asset_class == AssetClass.US_OPTION]
    underlying_tickers = {_underlying_from_occ_symbol(p.symbol) for p in option_positions}

    return len(underlying_tickers)


def _underlying_from_occ_symbol(symbol: str) -> str:
    """The ticker part of an OCC option symbol, e.g. "SPY" from "SPY260911C00717000"."""
    return symbol[:-15]


def place_debit_spread_order(plan: OrderPlan) -> Order:
    """
    Submit a sized debit spread as a real multi-leg limit order.

    The limit price is the same net debit per contract used for sizing
    (long leg ask minus short leg bid), so the order can't fill worse
    than what the 2%-of-equity risk budget was based on. If the market
    moves before the order posts, it may simply not fill right away
    rather than filling at a worse price.
    """
    limit_price = round(plan.est_cost_per_contract / 100, 2)

    order_request = LimitOrderRequest(
        qty=plan.contracts,
        order_class=OrderClass.MLEG,
        time_in_force=TimeInForce.DAY,
        limit_price=limit_price,
        legs=[
            OptionLegRequest(
                symbol=plan.spread.long_leg.symbol,
                ratio_qty=1,
                side=OrderSide.BUY,
            ),
            OptionLegRequest(
                symbol=plan.spread.short_leg.symbol,
                ratio_qty=1,
                side=OrderSide.SELL,
            ),
        ],
    )

    client = get_trading_client()
    return client.submit_order(order_request)


def get_open_debit_spreads() -> dict[str, dict[str, Position]]:
    """
    Group open option positions by underlying ticker.

    Returns {ticker: {"long": Position, "short": Position}}. A ticker
    with only one leg open (unexpected, but possible if one leg got
    closed on its own) is left out, since evaluate_exit() needs both
    legs to compute the spread's current value.
    """
    client = get_trading_client()
    positions = client.get_all_positions()
    option_positions = [p for p in positions if p.asset_class == AssetClass.US_OPTION]

    grouped: dict[str, dict[str, Position]] = {}
    for position in option_positions:
        ticker = _underlying_from_occ_symbol(position.symbol)
        leg = "long" if position.side == PositionSide.LONG else "short"
        grouped.setdefault(ticker, {})[leg] = position

    return {ticker: legs for ticker, legs in grouped.items() if "long" in legs and "short" in legs}


def close_debit_spread(long_symbol: str, short_symbol: str, qty: int) -> None:
    """
    Close both legs of a debit spread by symbol.

    Uses explicit closing orders (submit_order with position_intent set)
    rather than Alpaca's close_position() convenience method. Confirmed
    live: close_position() fails with "account not eligible to trade
    uncovered option contracts" even for a plain sell-to-close of a long
    option with zero short positions anywhere in the account. It doesn't
    tag the order's position_intent, and this account's options approval
    level (3: spreads, not 4: uncovered) apparently needs that explicit
    tag to recognize the order as closing rather than potentially
    opening a naked position. Setting position_intent directly fixes it.

    Closes the short leg first, then the long leg, as further defense in
    depth (closing a short can never increase short exposure). Waits for
    the short leg's close order to actually FILL before submitting the
    long leg's close: confirmed live that a fixed short sleep isn't
    enough - the short position still legitimately exists (and covers
    the long) until that order fills, so submitting the long leg's close
    too early gets rejected as uncovered. This matters especially outside
    active market hours, when a resting limit order may not fill for a
    while (or at all, until the next session).

    Prices both legs to be immediately marketable (sell the long at its
    bid, buy back the short at its ask) since an exit should execute
    promptly rather than wait for a better price, unlike an entry.
    """
    quotes = fetch_option_quotes([long_symbol, short_symbol])
    long_bid = quotes[long_symbol][0]
    short_ask = quotes[short_symbol][1]

    client = get_trading_client()

    short_close_order = client.submit_order(
        LimitOrderRequest(
            symbol=short_symbol,
            qty=qty,
            side=OrderSide.BUY,
            type="limit",
            time_in_force=TimeInForce.DAY,
            limit_price=short_ask,
            position_intent=PositionIntent.BUY_TO_CLOSE,
        )
    )

    filled = _wait_for_fill(client, short_close_order.id)
    if not filled:
        print(f"Short leg close for {short_symbol} did not fill in time; "
              f"leaving the long leg open rather than risk an uncovered "
              f"rejection. It'll be retried next run.")
        return

    client.submit_order(
        LimitOrderRequest(
            symbol=long_symbol,
            qty=qty,
            side=OrderSide.SELL,
            type="limit",
            time_in_force=TimeInForce.DAY,
            limit_price=long_bid,
            position_intent=PositionIntent.SELL_TO_CLOSE,
        )
    )


def _wait_for_fill(client: TradingClient, order_id, timeout_seconds: int = 30, poll_seconds: int = 2) -> bool:
    """Poll an order until it's filled or the timeout elapses."""
    from alpaca.trading.enums import OrderStatus

    waited = 0.0
    while waited < timeout_seconds:
        order = client.get_order_by_id(order_id)
        if order.status == OrderStatus.FILLED:
            return True
        if order.status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
            return False
        time.sleep(poll_seconds)
        waited += poll_seconds
    return False
