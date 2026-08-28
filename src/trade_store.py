"""
Local record of currently open trades, keyed by ticker.

Alpaca's own position data has no idea about our strategy, in particular
the breakout level that triggered entry. This is a small local file to
remember that one detail between the run that opens a trade and a later
run that decides whether to close it. Everything else needed to check an
exit (current price, current option value) is asked from Alpaca fresh
each time, not stored here.

Kept separate from logs/journal.jsonl on purpose: the journal is an
append-only audit trail that's never edited, this file is current state
that gets overwritten and shrinks as trades close.
"""

import json
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "open_trades.json"


def load_open_trades() -> dict:
    """Return {ticker: {"direction": ..., "breakout_level": ...}}."""
    if not STORE_PATH.exists():
        return {}
    with STORE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_open_trade(ticker: str, direction: str, breakout_level: float) -> None:
    trades = load_open_trades()
    trades[ticker] = {"direction": direction, "breakout_level": breakout_level}
    _write(trades)


def remove_open_trade(ticker: str) -> None:
    trades = load_open_trades()
    trades.pop(ticker, None)
    _write(trades)


def _write(trades: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STORE_PATH.open("w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)
