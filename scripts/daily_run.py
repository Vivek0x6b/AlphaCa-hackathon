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
from scripts.health_check import main as run_health_check
from src.broker import get_trading_client
from src.equity_history import log_equity_snapshot
from scripts.update_readme_dashboard import main as update_readme_dashboard

EASTERN = ZoneInfo("America/New_York")


def is_trading_day(date) -> bool:
    """Ask Alpaca's own trading calendar, rather than guessing weekends
    and holidays ourselves."""
    client = get_trading_client()
    calendar = client.get_calendar(GetCalendarRequest(start=date, end=date))
    return len(calendar) > 0


REPO_ROOT = Path(__file__).resolve().parent.parent

# Only these generated files are ever auto-committed. Anything else in the
# working tree (code mid-edit, local notes) stays out of automatic commits.
DASHBOARD_FILES = ["README.md", "docs/equity_curve.png"]


def publish_dashboard(run_date) -> None:
    """Commit and push the regenerated dashboard files after the daily run,
    so GitHub shows the closing numbers without a manual push each day."""
    import os
    import subprocess

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True, timeout=120,
        )

    git("add", "--", *DASHBOARD_FILES)
    if git("diff", "--cached", "--quiet", "--", *DASHBOARD_FILES).returncode == 0:
        print("Dashboard unchanged, nothing to publish.")
        return

    message = (
        f"Daily dashboard update for {run_date}\n\n"
        "Automated commit from scripts/daily_run.py after the market-close run."
    )
    commit = git("commit", "-m", message, "--", *DASHBOARD_FILES)
    if commit.returncode != 0:
        print(f"Dashboard commit failed: {commit.stderr.strip()}")
        return

    push = git("push")
    if push.returncode != 0:
        print(f"Dashboard push failed: {push.stderr.strip()}")
        return
    print(f"Published dashboard for {run_date} to GitHub.")


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

    try:
        equity = float(get_trading_client().get_account().equity)
        log_equity_snapshot(equity)
        print(f"Logged equity snapshot for {run_date}: ${equity:,.2f}")
    except Exception:
        traceback.print_exc()
        print(f"Equity snapshot failed for {run_date}.")

    try:
        update_readme_dashboard()
        publish_dashboard(run_date)
    except Exception:
        traceback.print_exc()
        print(f"README dashboard update failed for {run_date}.")

    try:
        print(f"\nHealth check for {run_date}:")
        run_health_check()
    except Exception:
        traceback.print_exc()
        print(f"Health check itself failed for {run_date}.")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
