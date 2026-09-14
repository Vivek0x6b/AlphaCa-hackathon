"""
One-shot diagnostic: is the live system actually in a healthy state?

Built after a week of finding real problems (a dead scheduler, a
duplicate entry, a failed stop-loss close) only by manually checking
several different things by hand each time. This automates that check:
scheduled task status, tracking-vs-Alpaca consistency, and any
unresolved error entries from today's journal. Read-only - never places
an order or changes any file.

Run it directly (python scripts/health_check.py) any time you want a
fast, complete answer to "is anything broken right now."
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.broker import get_trading_client, get_tickers_with_any_option_exposure, has_pending_order
from src.trade_store import load_open_trades

JOURNAL_PATH = Path(__file__).resolve().parent.parent / "logs" / "journal.jsonl"


def check_scheduled_task() -> list[str]:
    issues = []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTaskInfo -TaskName 'AlphaCa-DailyRun' | ConvertTo-Json"],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
    except Exception as exc:
        issues.append(f"Could not read the scheduled task's status: {exc}")
        return issues

    last_result = info.get("LastTaskResult")
    if last_result not in (0, None):
        issues.append(f"Scheduled task's last run failed (result code {last_result}).")

    return issues


def check_tracking_consistency() -> list[str]:
    issues = []
    open_trades = load_open_trades()
    exposed_tickers = get_tickers_with_any_option_exposure()

    for ticker in open_trades:
        if ticker in exposed_tickers:
            continue
        if has_pending_order(ticker):
            continue
        issues.append(
            f"{ticker} is tracked as an open trade but has no position and "
            f"no pending order on Alpaca - tracking may be stale."
        )

    for ticker in exposed_tickers:
        if ticker not in open_trades:
            issues.append(
                f"{ticker} has real option exposure on Alpaca but isn't in "
                f"open_trades.json - it won't get exit-checked."
            )

    return issues


def check_todays_journal_errors() -> list[str]:
    issues = []
    if not JOURNAL_PATH.exists():
        return issues

    today = date.today().isoformat()
    with JOURNAL_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            if not entry["timestamp"].startswith(today):
                continue
            if entry["type"] == "ticker_error":
                issues.append(f"ticker_error today: {entry['data']}")
            elif entry["type"] == "partial_close":
                issues.append(f"partial_close today (leg didn't fully close): {entry['data']}")

    return issues


def main():
    all_issues = []
    all_issues += check_scheduled_task()
    all_issues += check_tracking_consistency()
    all_issues += check_todays_journal_errors()

    client = get_trading_client()
    account = client.get_account()

    print(f"Equity: ${float(account.equity):,.2f}")
    print(f"Open trades tracked: {list(load_open_trades().keys())}")

    if not all_issues:
        print("\nPASS - no issues found.")
    else:
        print(f"\nWARN - {len(all_issues)} issue(s) found:")
        for issue in all_issues:
            print(f"  - {issue}")


if __name__ == "__main__":
    main()
