"""
Dry-run a debit spread for one ticker/direction, without waiting for a
real breakout signal and without placing any order.

Useful for testing the chain-fetching, sizing, and payload-building code
against real live Alpaca data on any day, since real breakouts don't
happen every day.

Usage:
    python scripts/dry_run_spread.py SPY call
    python scripts/dry_run_spread.py AAPL put
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.watchlist import MIN_DAYS_TO_EXPIRY, MAX_DAYS_TO_EXPIRY
from src.market_data import fetch_option_chain, fetch_option_quotes
from src.broker import get_account_equity, get_open_spread_count
from src.options_selector import select_debit_spread
from src.execution import size_position, build_order_payload


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in ("call", "put"):
        print("Usage: python scripts/dry_run_spread.py TICKER call|put")
        sys.exit(1)

    ticker, direction = sys.argv[1].upper(), sys.argv[2]

    print(f"Pretending {ticker} fired a {direction} signal (no real signal check).\n")

    print("Fetching option chain...")
    option_chain = fetch_option_chain(ticker, direction)
    print(f"  {len(option_chain)} contracts in the "
          f"{MIN_DAYS_TO_EXPIRY}-{MAX_DAYS_TO_EXPIRY} day expiry window.\n")

    spread = select_debit_spread(ticker, direction, option_chain)
    if spread is None:
        print("No suitable spread found (no contracts matched the delta/expiry rules).")
        return
    print("Spread selected:")
    print(f"  {spread.reasoning}\n")

    print("Fetching live quotes for both legs...")
    quotes = fetch_option_quotes([spread.long_leg.symbol, spread.short_leg.symbol])
    long_leg_price = quotes[spread.long_leg.symbol][1]  # ask, we're buying
    short_leg_price = quotes[spread.short_leg.symbol][0]  # bid, we're selling
    print(f"  Long leg {spread.long_leg.symbol}: ask ${long_leg_price}")
    print(f"  Short leg {spread.short_leg.symbol}: bid ${short_leg_price}\n")

    print("Checking account equity and open positions...")
    equity = get_account_equity()
    open_count = get_open_spread_count()
    print(f"  Equity: ${equity:,.2f}, open spreads: {open_count}\n")

    plan = size_position(
        spread,
        account_equity=equity,
        long_leg_price=long_leg_price,
        short_leg_price=short_leg_price,
        open_position_count=open_count,
    )
    if plan is None:
        print("Sizing rejected the trade (position limit reached or risk budget too small).")
        return

    print("Order plan:")
    print(f"  {plan.reasoning}\n")

    payload = build_order_payload(plan)
    print("Order payload (NOT sent, this is a dry run):")
    print(f"  {payload}")


if __name__ == "__main__":
    main()
