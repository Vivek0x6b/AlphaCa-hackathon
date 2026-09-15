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


def get_tickers_with_any_option_exposure() -> set[str]:
    """
    Every underlying ticker with ANY open option position at all,
    matched pair or not.

    Confirmed live: get_open_debit_spreads() (matched pairs only) and
    even get_orphaned_option_legs() (exactly one leg) can both miss a
    ticker that's in a messier state - e.g. two different short strikes
    open at once after a duplicate entry. A ticker in ANY such state
    must never be treated as free to enter again. This is a broader,
    simpler safety net than trying to enumerate every possible position
    shape: if Alpaca shows any option exposure on this ticker at all,
    it's not available for a new entry, full stop.
    """
    client = get_trading_client()
    positions = client.get_all_positions()
    option_positions = [p for p in positions if p.asset_class == AssetClass.US_OPTION]
    return {_underlying_from_occ_symbol(p.symbol) for p in option_positions}


def has_pending_order(ticker: str) -> bool:
    """
    True if there's any still-open (unfilled) order on an option whose
    underlying is this ticker.

    Needed because a just-placed entry order can still be sitting
    unfilled (e.g. placed right after close, DAY order waiting for the
    next session) when a later check runs. With zero positions open yet,
    that looks identical to "already fully closed" unless we also check
    for a pending order - without this, a trade that hasn't even entered
    yet would get silently dropped from tracking.
    """
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    client = get_trading_client()
    open_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    for order in open_orders:
        symbols = [leg.symbol for leg in order.legs] if order.legs else [order.symbol]
        if any(_underlying_from_occ_symbol(s) == ticker for s in symbols if s):
            return True
    return False


def get_orphaned_option_legs() -> dict[str, Position]:
    """
    Tickers with exactly one open option leg, not a matched pair.

    This happens when a spread close completes one leg (e.g. the short
    leg's close order finally fills after sitting unfilled for days) but
    the other leg's close never went out in the same run - the position
    is real and still open, just no longer visible to
    get_open_debit_spreads() since that requires both legs. Confirmed
    live: this is NOT the same as "both legs already closed" and must
    not be treated that way - a lone leftover leg still needs to be
    closed, not silently dropped from tracking.
    """
    client = get_trading_client()
    positions = client.get_all_positions()
    option_positions = [p for p in positions if p.asset_class == AssetClass.US_OPTION]

    grouped: dict[str, list[Position]] = {}
    for position in option_positions:
        ticker = _underlying_from_occ_symbol(position.symbol)
        grouped.setdefault(ticker, []).append(position)

    return {ticker: legs[0] for ticker, legs in grouped.items() if len(legs) == 1}


def get_all_legs_for_ticker(ticker: str) -> list[Position]:
    """
    Every open option position for one underlying ticker, however many
    there are.

    Unlike get_open_debit_spreads() (exactly one long + one short) or
    get_orphaned_option_legs() (exactly one leg total), this makes no
    assumption about shape - it's the general case, needed because a
    duplicate-entry bug can leave a ticker with two different long
    strikes and two different short strikes open at once. Any P&L or
    close logic that only looks at "the" long/short leg will silently
    use the wrong numbers (or the wrong quantity) whenever a ticker is
    in one of these messier states - confirmed live, this is exactly
    what caused a stop-loss close to fail with a quantity mismatch.
    """
    client = get_trading_client()
    positions = client.get_all_positions()
    return [
        p for p in positions
        if p.asset_class == AssetClass.US_OPTION and _underlying_from_occ_symbol(p.symbol) == ticker
    ]


def close_all_legs_for_ticker(ticker: str) -> bool:
    """
    Close every open option leg for a ticker, however many there are.

    Closes all short legs first (waiting for each to actually fill),
    then all long legs - same safety ordering as close_debit_spread(),
    generalized to any number of legs per side instead of assuming
    exactly one. Returns True only if every leg closed.
    """
    legs = get_all_legs_for_ticker(ticker)
    shorts = [p for p in legs if p.side == PositionSide.SHORT]
    longs = [p for p in legs if p.side == PositionSide.LONG]

    for leg in shorts:
        qty = abs(int(float(leg.qty)))
        if not close_single_leg(leg.symbol, qty, PositionSide.SHORT):
            return False

    for leg in longs:
        qty = abs(int(float(leg.qty)))
        if not close_single_leg(leg.symbol, qty, PositionSide.LONG):
            return False

    return True


def close_single_leg(symbol: str, qty: int, side: PositionSide) -> bool:
    """
    Close one standalone option leg (not part of a matched spread).

    side is the position's current side (LONG closes via SELL_TO_CLOSE,
    SHORT closes via BUY_TO_CLOSE). Cancels any stale resting close order
    first and reprices fresh, same reasoning as before: a limit price
    quoted days ago can be far from the market.

    Uses GTC (good-til-canceled), not DAY. Confirmed live: the daily run
    happens at 4:15pm ET, right after the close, so a DAY close order has
    essentially no window to fill same-day - and confirmed live, it does
    NOT get a fresh shot at the next session's open either, it just
    silently expires exactly at the next session boundary without ever
    trying to fill. A position that needs to exit would sit unmanaged
    indefinitely under DAY, only actually closing on days someone happens
    to intervene manually during real market hours. GTC actually rests
    through the next session instead of vanishing at the boundary.
    """
    client = get_trading_client()
    quotes = fetch_option_quotes([symbol])
    bid, ask = quotes[symbol]

    _cancel_open_orders(client, symbol)

    if side == PositionSide.LONG:
        order_side, limit_price, intent = OrderSide.SELL, bid, PositionIntent.SELL_TO_CLOSE
    else:
        order_side, limit_price, intent = OrderSide.BUY, ask, PositionIntent.BUY_TO_CLOSE

    order = client.submit_order(
        LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            type="limit",
            time_in_force=TimeInForce.GTC,
            limit_price=limit_price,
            position_intent=intent,
        )
    )
    return _wait_for_fill(client, order.id)


def _cancel_open_orders(client: TradingClient, symbol: str) -> None:
    """Cancel any still-open order on this symbol, so a fresh one can be
    submitted without "insufficient qty" from a stale one holding it."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    open_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol]))
    for order in open_orders:
        client.cancel_order_by_id(order.id)


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
