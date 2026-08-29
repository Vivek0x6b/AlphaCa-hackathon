"""
Runs the agent loop once per trading day, shortly after market close.

This is the "simple Python scheduler" fallback for running run_agent.py
outside Hermes (see CLAUDE_CODE_CONTEXT.md). A long-running process:
start it once and leave it running. Each day it sleeps until the
scheduled run time, checks with Alpaca's own trading calendar whether
today was actually a trading day, and if so runs the agent loop once.

The strategy only uses daily bars, so running more than once a day would
just re-check the same, unchanged bar. Hermes' own cronjob tool replaces
this entirely for the live autonomous version.
"""

import sys
import time
import traceback
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.trading.requests import GetCalendarRequest

from scripts.run_agent import run_once
from scripts.retune import run_retune
from src.broker import get_trading_client

EASTERN = ZoneInfo("America/New_York")

# A few minutes after the 4:00pm ET close, so the day's final daily bar
# is fully settled at Alpaca before we ask for it.
RUN_TIME = dtime(16, 15)


def next_run_at(now: datetime) -> datetime:
    """The next occurrence of RUN_TIME, today if it hasn't passed yet."""
    candidate = now.replace(
        hour=RUN_TIME.hour, minute=RUN_TIME.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def is_trading_day(date) -> bool:
    """Ask Alpaca's own trading calendar, rather than guessing weekends
    and holidays ourselves."""
    client = get_trading_client()
    calendar = client.get_calendar(GetCalendarRequest(start=date, end=date))
    return len(calendar) > 0


def run_forever():
    while True:
        now = datetime.now(EASTERN)
        target = next_run_at(now)
        sleep_seconds = (target - now).total_seconds()
        print(f"Sleeping until {target.isoformat()} "
              f"({sleep_seconds / 3600:.1f} hours)...")
        time.sleep(sleep_seconds)

        run_date = datetime.now(EASTERN).date()
        if is_trading_day(run_date):
            # Each step is its own try/except so a bug or API hiccup on
            # one day logs and moves on to tomorrow, instead of silently
            # killing the whole scheduler process (leaving trading dark
            # for days with nobody noticing).
            try:
                print(f"Re-tuning strategy parameters for {run_date}...")
                run_retune()
            except Exception:
                traceback.print_exc()
                print(f"Re-tune failed for {run_date}, continuing to trading pass.")

            try:
                print(f"Running agent loop for {run_date}...")
                run_once()
            except Exception:
                traceback.print_exc()
                print(f"Trading run failed for {run_date}. Will retry tomorrow.")
        else:
            print(f"{run_date} is not a trading day. Skipping.")


if __name__ == "__main__":
    # Flush every print immediately, so status is visible right away even
    # if output is piped to a file instead of a live terminal.
    sys.stdout.reconfigure(line_buffering=True)
    run_forever()
