"""
Mutable, autonomously-tunable strategy parameters.

Separate from config/watchlist.py on purpose. config/watchlist.py holds
values a human sets and commits: the watchlist, position sizing, signal
detection rules, and whether puts are traded at all (PUT_TRADING_ENABLED
stays a human decision, see its comment in config/watchlist.py for why).

This file holds the narrower set of bounded numeric parameters the daily
autonomous re-tune job is allowed to adjust on its own: delta ranges, the
expiry window, and exit thresholds. An autonomous process shouldn't rewrite
live .py source on disk, so these live in a separate JSON file that both
the live strategy and the re-tuner read/write at runtime.
"""

import json
from pathlib import Path

from config.watchlist import (
    LONG_LEG_DELTA_RANGE,
    SHORT_LEG_DELTA_RANGE,
    MIN_DAYS_TO_EXPIRY,
    MAX_DAYS_TO_EXPIRY,
)
from src.position_manager import PROFIT_TARGET_PCT, STOP_LOSS_PCT

PARAMS_PATH = Path(__file__).resolve().parent.parent / "data" / "strategy_params.json"

DEFAULT_PARAMS = {
    "long_leg_delta_range": list(LONG_LEG_DELTA_RANGE),
    "short_leg_delta_range": list(SHORT_LEG_DELTA_RANGE),
    "min_days_to_expiry": MIN_DAYS_TO_EXPIRY,
    "max_days_to_expiry": MAX_DAYS_TO_EXPIRY,
    "profit_target_pct": PROFIT_TARGET_PCT,
    "stop_loss_pct": STOP_LOSS_PCT,
}


def load_params() -> dict:
    """
    Current tunable parameters. Falls back to config/watchlist.py's and
    position_manager.py's defaults if no file exists yet (first run).

    Delta ranges come back as tuples (JSON only has lists, but every
    consumer expects a (low, high) tuple).
    """
    if not PARAMS_PATH.exists():
        params = dict(DEFAULT_PARAMS)
    else:
        with PARAMS_PATH.open("r", encoding="utf-8") as f:
            params = json.load(f)

    params["long_leg_delta_range"] = tuple(params["long_leg_delta_range"])
    params["short_leg_delta_range"] = tuple(params["short_leg_delta_range"])
    return params


def save_params(params: dict) -> None:
    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PARAMS_PATH.open("w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)
