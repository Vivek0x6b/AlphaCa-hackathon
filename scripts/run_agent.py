"""
Main entry point for a single agent loop pass.

This is the script the cron job (or a manual run) triggers. It:
  1. Checks open positions for exit conditions, closing any that hit
     their profit target, stop loss, or thesis invalidation
  2. Pulls bars for the watchlist
  3. Runs signal detection
  4. For any fired signal, selects a debit spread, sizes it, and places
     the order
  5. Journals every decision along the way

This version calls Alpaca directly via alpaca-py. Wiring the same logic
into Hermes' MCP tools, for the live autonomous loop, is a separate later
step.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.trading.enums import OrderStatus

from config.watchlist import WATCHLIST, PUT_TRADING_ENABLED
from src.market_data import fetch_bars, fetch_option_chain, fetch_option_quotes, fetch_stock_prices
from src.broker import (
    get_account_equity,
    get_open_spread_count,
    get_open_debit_spreads,
    place_debit_spread_order,
    close_debit_spread,
)
from src.signals import scan_watchlist
from src.options_selector import select_debit_spread
from src.execution import size_position, build_order_payload
from src.position_manager import evaluate_exit
from src.trade_store import load_open_trades, save_open_trade, remove_open_trade
from src.strategy_params import load_params
from src.journal import log_entry


def check_exits(params: dict):
    """Check every open spread we have metadata for and close any that
    should exit (profit target, stop loss, or thesis invalidation)."""
    open_trades = load_open_trades()
    if not open_trades:
        print("No open trades to check.")
        return

    spreads = get_open_debit_spreads()
    current_prices = fetch_stock_prices(list(open_trades.keys()))

    for ticker, meta in open_trades.items():
        if ticker not in spreads:
            # We think we have a trade open, but Alpaca shows no matching
            # pair of legs (e.g. it was closed manually). Drop our record
            # of it rather than checking a trade that no longer exists.
            remove_open_trade(ticker)
            continue

        long_leg = spreads[ticker]["long"]
        short_leg = spreads[ticker]["short"]

        entry_debit = (float(long_leg.avg_entry_price) - float(short_leg.avg_entry_price)) * 100
        current_value = (float(long_leg.current_price) - float(short_leg.current_price)) * 100

        decision = evaluate_exit(
            ticker=ticker,
            direction=meta["direction"],
            entry_debit=entry_debit,
            current_value=current_value,
            current_price=current_prices[ticker],
            breakout_level=meta["breakout_level"],
            profit_target_pct=params["profit_target_pct"],
            stop_loss_pct=params["stop_loss_pct"],
        )
        log_entry("exit_check", decision)
        print(f"[{ticker}] {decision.reasoning}")

        if decision.should_exit:
            qty = abs(int(float(long_leg.qty)))
            close_debit_spread(long_leg.symbol, short_leg.symbol, qty)
            remove_open_trade(ticker)
            log_entry("trade_exit", decision)
            print(f"[{ticker}] closed both legs.")


def run_once():
    params = load_params()

    print("Checking open positions for exits...")
    check_exits(params)

    print(f"Scanning {len(WATCHLIST)} tickers: {', '.join(WATCHLIST)}")

    bars_by_ticker = fetch_bars(WATCHLIST)

    if not bars_by_ticker:
        print("No bar data returned from Alpaca. Skipping this run.")
        return

    results = scan_watchlist(bars_by_ticker)

    # Fetched once per run, then tracked locally as trades open below. A
    # freshly placed order won't have filled yet, so re-fetching this
    # from Alpaca on every loop iteration wouldn't see it - meaning if
    # two tickers fire in the same run, the position limit wouldn't
    # actually stop a third or fourth entry. Tracking it locally fixes
    # that.
    open_position_count = get_open_spread_count()

    # Tickers already carrying a position never get a second one. Checked
    # against both our own trade record and Alpaca's real positions (a
    # ticker can be missing from one but not the other after a bug or
    # manual intervention), fetched fresh after check_exits() above so a
    # position closed this run frees up its ticker again the same day.
    already_open_tickers = set(load_open_trades()) | set(get_open_debit_spreads())

    for result in results:
        log_entry("signal_check", result)
        print(f"[{result.ticker}] fired={result.fired}: {result.reasoning}")

        if not result.fired:
            continue

        if result.ticker in already_open_tickers:
            print(f"[{result.ticker}] signal fired but this ticker already has "
                  f"an open position. Skipping to avoid doubling up.")
            continue

        if result.direction == "put" and not PUT_TRADING_ENABLED:
            print(f"[{result.ticker}] put signal fired but put trading is disabled "
                  f"(backtesting found puts underperform calls). Not trading it.")
            continue

        option_chain = fetch_option_chain(
            result.ticker,
            result.direction,
            min_days_to_expiry=params["min_days_to_expiry"],
            max_days_to_expiry=params["max_days_to_expiry"],
        )

        spread = select_debit_spread(
            result.ticker,
            result.direction,
            option_chain,
            long_leg_delta_range=params["long_leg_delta_range"],
            short_leg_delta_range=params["short_leg_delta_range"],
            min_days_to_expiry=params["min_days_to_expiry"],
            max_days_to_expiry=params["max_days_to_expiry"],
        )
        if spread is None:
            print(f"[{result.ticker}] signal fired but no suitable spread found.")
            continue

        log_entry("spread_selected", spread)
        print(f"[{result.ticker}] {spread.reasoning}")

        quotes = fetch_option_quotes([spread.long_leg.symbol, spread.short_leg.symbol])
        # Buying the long leg: use the ask (the price we'd actually pay).
        # Selling the short leg: use the bid (the price we'd actually receive).
        long_leg_price = quotes[spread.long_leg.symbol][1]
        short_leg_price = quotes[spread.short_leg.symbol][0]

        plan = size_position(
            spread,
            account_equity=get_account_equity(),
            long_leg_price=long_leg_price,
            short_leg_price=short_leg_price,
            open_position_count=open_position_count,
        )
        if plan is None:
            print(f"[{result.ticker}] spread found but sizing rejected it "
                  f"(position limit reached or risk budget too small).")
            continue

        log_entry("trade_entry", plan)
        print(f"[{result.ticker}] {plan.reasoning}")

        payload = build_order_payload(plan)
        log_entry("order_payload_built", payload)

        order = place_debit_spread_order(plan)
        log_entry("order_submitted", {"order_id": str(order.id), "status": str(order.status)})
        print(f"[{result.ticker}] order submitted, id={order.id}, status={order.status}")

        # Alpaca can return an order that was never actually accepted
        # (rejected/canceled/expired) without raising an exception. Only
        # count it toward the same-run position limit, and only remember
        # it for later exit-checking, if it's actually live.
        if order.status in (OrderStatus.REJECTED, OrderStatus.CANCELED, OrderStatus.EXPIRED):
            print(f"[{result.ticker}] order was not accepted (status={order.status}), "
                  f"not counting it toward the position limit.")
        else:
            save_open_trade(result.ticker, result.direction, result.breakout_level)
            open_position_count += 1


if __name__ == "__main__":
    run_once()
