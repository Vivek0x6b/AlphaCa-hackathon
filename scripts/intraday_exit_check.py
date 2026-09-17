"""
Checks open positions for exit conditions only - no entry scan, no
re-tune. Meant to run frequently during market hours (every 15 minutes,
via a Windows Scheduled Task), separate from daily_run.py's once-daily
full cycle.

Why this exists: the strategy's exit decision (profit target, stop loss,
thesis invalidation) is already based on Alpaca's live position marks,
not on daily bars - nothing about check_exits() actually requires waiting
for the day's close. But it only ever ran once a day at 4:15pm, so a
position that hit its profit target at 11am sat unclosed for hours,
giving back gains, purely because nothing checked it sooner. Entry
signals genuinely do need a confirmed daily close (that's what the
breakout/trend/volume logic is defined against) so those stay on the
once-daily cycle unchanged.
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.trading.requests import GetCalendarRequest

from scripts.run_agent import check_exits
from src.strategy_params import load_params
from src.broker import get_trading_client

EASTERN = ZoneInfo("America/New_York")


def is_trading_day(date) -> bool:
    client = get_trading_client()
    calendar = client.get_calendar(GetCalendarRequest(start=date, end=date))
    return len(calendar) > 0


def main():
    today = datetime.now(EASTERN).date()
    if not is_trading_day(today):
        print(f"{today} is not a trading day. Skipping.")
        return

    check_exits(load_params())


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
