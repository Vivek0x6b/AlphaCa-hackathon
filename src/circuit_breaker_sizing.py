"""
Circuit-breaker position sizing: shrink the next trade's size after a
losing streak, from the same paper the ATR-adaptive stops idea came from
("Optimal Stop-Loss and Take-Profit Parameterization," arXiv:2604.27150),
which specifically found a reduction factor of 0.25 after 2 consecutive
losses effective for its instrument. Tested here as its own idea, since
this project's earlier test of that paper only covered its ATR-stop
component (rejected), not this one.

Different mechanism from every volatility-based idea already tried: this
reacts to the strategy's own recent run of outcomes, not any measure of
market or ticker volatility.
"""


def consecutive_losses(closed_trade_pnls: list[float]) -> int:
    """How many of the most recent trades, counting back from the end,
    were losses in a row (0 if the most recent trade was a win)."""
    streak = 0
    for pnl in reversed(closed_trade_pnls):
        if pnl <= 0:
            streak += 1
        else:
            break
    return streak


def circuit_breaker_multiplier(
    closed_trade_pnls: list[float],
    loss_streak_threshold: int = 2,
    reduction_factor: float = 0.25,
) -> float:
    """1.0 (no change) unless the current losing streak has reached
    loss_streak_threshold, in which case size is cut to reduction_factor
    of normal until a win breaks the streak."""
    if consecutive_losses(closed_trade_pnls) >= loss_streak_threshold:
        return reduction_factor
    return 1.0
