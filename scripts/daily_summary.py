"""
Extract today's journal entries into a clean, structured summary.

Meant to be run as a pre-step before Hermes' daily narration job (see
docs/hermes-integration.md): this script does the actual data filtering
deterministically, so the LLM only has to turn already-correct structured
data into prose, instead of parsing raw JSONL and filtering by date
itself. More reliable, and cheaper as the journal grows over the
competition week.
"""

import json
import sys
from datetime import date
from pathlib import Path

JOURNAL_PATH = Path(__file__).resolve().parent.parent / "logs" / "journal.jsonl"


def load_today_entries(today: date) -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []

    entries = []
    with JOURNAL_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            entry_date = record["timestamp"][:10]
            if entry_date == today.isoformat():
                entries.append(record)
    return entries


def summarize(entries: list[dict]) -> dict:
    retune = next((e["data"] for e in entries if e["type"] in ("retune_decision", "retune_check")), None)

    fired_tickers = {}
    for e in entries:
        if e["type"] == "signal_check" and e["data"]["fired"]:
            fired_tickers[e["data"]["ticker"]] = {"signal": e["data"]}
    for e in entries:
        if e["type"] == "spread_selected" and e["data"]["ticker"] in fired_tickers:
            fired_tickers[e["data"]["ticker"]]["spread"] = e["data"]
        if e["type"] == "trade_entry" and e["data"]["spread"]["ticker"] in fired_tickers:
            fired_tickers[e["data"]["spread"]["ticker"]]["sizing"] = e["data"]
        if e["type"] == "order_submitted":
            # order_submitted entries don't carry the ticker directly, so
            # attach to whichever ticker doesn't have an order yet.
            for ticker, info in fired_tickers.items():
                if "sizing" in info and "order" not in info:
                    info["order"] = e["data"]
                    break

    checked_tickers = [e["data"]["ticker"] for e in entries if e["type"] == "signal_check"]

    return {
        "retune": retune,
        "fired_tickers": fired_tickers,
        "checked_tickers": checked_tickers,
        "entry_count": len(entries),
    }


if __name__ == "__main__":
    today = date.today()
    entries = load_today_entries(today)
    summary = summarize(entries)
    print(json.dumps({"date": today.isoformat(), **summary}, indent=2, default=str))
