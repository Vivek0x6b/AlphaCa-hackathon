"""
Runs the agent loop exactly once, meant to be invoked by a Windows
Scheduled Task at 4:15pm ET on trading days, rather than by a long-lived
Python process (see scheduler.py).

Confirmed live: a permanently-running scheduler process has two real
failure modes - it silently dies on a machine reboot (nothing restarts
it, nothing alerts anyone), and it holds every module in memory from
whenever it started, so a code fix made hours or days later has zero
effect until someone remembers to manually restart it. A fresh process
per run, triggered by the OS's own task scheduler (which survives
reboots and always starts a clean interpreter), avoids both problems
entirely. See docs/scheduled-task-setup.md for how this gets registered.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.trading.requests import GetCalendarRequest

from scripts.run_agent import run_once
from scripts.retune import run_retune
from src.broker import get_trading_client

EASTERN = ZoneInfo("America/New_York")


def is_trading_day(date) -> bool:
    """Ask Alpaca's own trading calendar, rather than guessing weekends
    and holidays ourselves."""
    client = get_trading_client()
    calendar = client.get_calendar(GetCalendarRequest(start=date, end=date))
    return len(calendar) > 0


def main():
    run_date = datetime.now(EASTERN).date()

    if not is_trading_day(run_date):
        print(f"{run_date} is not a trading day. Skipping.")
        return

    # Each step is its own try/except so a bug or API hiccup on one
    # doesn't stop the other from running today.
    try:
        print(f"Re-tuning strategy parameters for {run_date}...")
        run_retune()
    except Exception:
        traceback.print_exc()
        print(f"Re-tune failed for {run_date}.")

    try:
        print(f"Running agent loop for {run_date}...")
        run_once()
    except Exception:
        traceback.print_exc()
        print(f"Trading run failed for {run_date}.")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
