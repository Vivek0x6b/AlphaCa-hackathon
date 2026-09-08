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
    reason: Literal["profit_target", "trailing_stop", "stop_loss", "thesis_invalidated", "hold", None]
    reasoning: str
    peak_value: float = 0.0  # highest current_value seen since entry, for trailing-stop tracking


def evaluate_exit(
    ticker: str,
    direction: Literal["call", "put"],
    entry_debit: float,
    current_value: float,
    current_price: float,
    breakout_level: float,
    profit_target_pct: float = PROFIT_TARGET_PCT,
    stop_loss_pct: float = STOP_LOSS_PCT,
    peak_value: float | None = None,
    trailing_stop_pct: float | None = None,
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
    peak_value: the highest current_value seen since entry, tracked by
        the caller and passed back in each check. None on the first
        check (peak starts at entry_debit). Ignored unless
        trailing_stop_pct is set.
    trailing_stop_pct: if set, replaces the flat profit_target exit with
        a trailing one - once the position's peak gain reaches
        profit_target_pct, it's left open to keep running and only
        closed if it pulls back this fraction from its peak. None (the
        default) keeps the original behavior: close immediately at
        profit_target_pct, no trailing.
    """
    peak_value = max(peak_value if peak_value is not None else entry_debit, current_value)
    pnl_pct = (current_value - entry_debit) / entry_debit if entry_debit else 0.0
    peak_pnl_pct = (peak_value - entry_debit) / entry_debit if entry_debit else 0.0

    if trailing_stop_pct is None:
        if pnl_pct >= profit_target_pct:
            return ExitDecision(
                symbol_group=ticker,
                should_exit=True,
                reason="profit_target",
                reasoning=(
                    f"{ticker} spread is up {pnl_pct:.0%}, at or above the "
                    f"{profit_target_pct:.0%} profit target. Closing."
                ),
                peak_value=peak_value,
            )
    elif peak_pnl_pct >= profit_target_pct:
        drawdown_from_peak = (peak_value - current_value) / peak_value if peak_value else 0.0
        if drawdown_from_peak >= trailing_stop_pct:
            return ExitDecision(
                symbol_group=ticker,
                should_exit=True,
                reason="trailing_stop",
                reasoning=(
                    f"{ticker} spread peaked at +{peak_pnl_pct:.0%} and has "
                    f"pulled back {drawdown_from_peak:.0%} from that peak, "
                    f"at or beyond the {trailing_stop_pct:.0%} trailing "
                    f"stop. Closing to lock in the gain."
                ),
                peak_value=peak_value,
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
            peak_value=peak_value,
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
            peak_value=peak_value,
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
        peak_value=peak_value,
    )
