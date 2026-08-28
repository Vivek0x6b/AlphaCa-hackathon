"""
Decision journal.

Every signal check, trade decision, and exit decision gets logged here,
with the reasoning behind it. This is the audit trail that backs up the
"clear testable thesis" the hackathon judging criteria ask for. Every
entry should read as a reason, not just an action.

Logs are written as JSON Lines (one JSON object per line) to
logs/journal.jsonl, which is gitignored (see .gitignore) since it's
runtime output, not source.
"""

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "journal.jsonl"


def log_entry(entry_type: str, data) -> None:
    """
    Append one journal entry.

    Parameters
    ----------
    entry_type: str
        e.g. "signal_check", "trade_entry", "trade_exit"
    data: dataclass instance or dict
        The result object (SignalResult, OrderPlan, ExitDecision, etc.)
        being logged. Dataclasses are converted to dicts automatically.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = asdict(data) if is_dataclass(data) else data

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        "data": payload,
    }

    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read_journal(limit: int | None = None) -> list[dict]:
    """Read journal entries back, most recent last. Useful for the
    dashboard / demo and for reviewing the agent's reasoning history."""
    if not LOG_PATH.exists():
        return []

    with LOG_PATH.open("r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    if limit:
        return lines[-limit:]
    return lines
