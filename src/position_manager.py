"""
Position management: exit conditions for open debit spreads.

Checks each open position against a profit target, a stop loss, and
thesis invalidation (price falls back through the original breakout
level). Pure logic in, decision plus reasoning out. Actual closing of
positions happens through Alpaca's close_position / cancel_order tools,
called from the agent loop or execution layer.
"""

from dataclasses import dataclass
from typing import Literal

# Exit thresholds, expressed as a fraction of the original net debit paid.
PROFIT_TARGET_PCT = 0.50  # close at +50% of debit paid
STOP_LOSS_PCT = 0.50      # close at -50% of debit paid


@dataclass
class ExitDecision:
    symbol_group: str  # ticker or spread identifier
    should_exit: bool
    reason: Literal["profit_target", "stop_loss", "thesis_invalidated", "hold", None]
    reasoning: str


def evaluate_exit(
    ticker: str,
    direction: Literal["call", "put"],
    entry_debit: float,
    current_value: float,
    current_price: float,
    breakout_level: float,
    profit_target_pct: float = PROFIT_TARGET_PCT,
    stop_loss_pct: float = STOP_LOSS_PCT,
) -> ExitDecision:
    """
    Decide whether an open spread position should be closed.

    Parameters
    ----------
    entry_debit: float
        Net debit originally paid per contract.
    current_value: float
        Current mark-to-market value per contract.
    current_price: float
        Current underlying price.
    breakout_level: float
        The breakout level that triggered entry. If price falls back
        through this level against the trade direction, the original
        thesis no longer holds.
    profit_target_pct, stop_loss_pct: default to this module's constants.
        Overridable so the backtester (and the autonomous re-tuner) can
        test different thresholds without touching live config.
    """
    pnl_pct = (current_value - entry_debit) / entry_debit if entry_debit else 0.0

    if pnl_pct >= profit_target_pct:
        return ExitDecision(
            symbol_group=ticker,
            should_exit=True,
            reason="profit_target",
            reasoning=(
                f"{ticker} spread is up {pnl_pct:.0%}, at or above the "
                f"{profit_target_pct:.0%} profit target. Closing."
            ),
        )

    if pnl_pct <= -stop_loss_pct:
        return ExitDecision(
            symbol_group=ticker,
            should_exit=True,
            reason="stop_loss",
            reasoning=(
                f"{ticker} spread is down {pnl_pct:.0%}, at or below the "
                f"-{stop_loss_pct:.0%} stop loss. Closing to limit further "
                f"loss."
            ),
        )

    thesis_broken = (
        direction == "call" and current_price < breakout_level
    ) or (
        direction == "put" and current_price > breakout_level
    )
    if thesis_broken:
        return ExitDecision(
            symbol_group=ticker,
            should_exit=True,
            reason="thesis_invalidated",
            reasoning=(
                f"{ticker} price ({current_price:.2f}) has moved back "
                f"through the original breakout level "
                f"({breakout_level:.2f}). The thesis that triggered this "
                f"trade no longer holds. Closing."
            ),
        )

    return ExitDecision(
        symbol_group=ticker,
        should_exit=False,
        reason="hold",
        reasoning=(
            f"{ticker} spread P/L is {pnl_pct:+.0%}, within target/stop "
            f"range, and price is still consistent with the original "
            f"thesis. Holding."
        ),
    )
