"""
Daily equity snapshot - the time-series data backbone for a future
dashboard.

Kept separate from journal.py's decision log on purpose: this is a simple,
narrow time series (one number per day), not a decision with reasoning
behind it. A dashboard wanting to plot "P&L over time" needs exactly this
file, not the whole decision journal.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "equity_history.jsonl"


def log_equity_snapshot(equity: float) -> None:
    """
    Append today's equity. Safe to call more than once a day (e.g. a
    manual run in addition to the scheduled one) - overwrites today's
    existing snapshot rather than appending a duplicate, so the history
    stays one point per day.
    """
    today = date.today().isoformat()
    history = read_equity_history()
    history = [h for h in history if h["date"] != today]
    history.append({
        "date": today,
        "equity": equity,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    history.sort(key=lambda h: h["date"])

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8") as f:
        for entry in history:
            f.write(json.dumps(entry) + "\n")


def read_equity_history() -> list[dict]:
    """[{date, equity, recorded_at}, ...], oldest first."""
    if not LOG_PATH.exists():
        return []

    with LOG_PATH.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
