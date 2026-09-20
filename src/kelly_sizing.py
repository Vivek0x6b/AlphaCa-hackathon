"""
Kelly-criterion position sizing, adapted from "Tackling estimation risk in
Kelly investing using options" (arXiv:2508.18868).

The live strategy risks a flat 8% of equity on every trade, regardless of
how good the strategy's actual track record looks. Kelly sizing instead
computes the fraction of equity to risk from the strategy's own win rate
and win/loss payoff ratio: f* = (b*p - q) / b, where p is win probability,
q = 1-p, and b is the average-win/average-loss ratio.

Full Kelly is well known to be fragile when the win-rate/payoff estimates
feeding it are noisy (exactly the situation here - the live track record
has a small sample). This paper's actual contribution is a way to hedge
that estimation risk; the simplest, most defensible version of that idea
without needing an options-hedging model of its own is a FRACTIONAL Kelly
(risk only kelly_multiplier of the computed optimal fraction) plus a
minimum sample size before trusting the estimate at all - both standard,
well-documented risk controls for exactly this kind of estimation risk.
"""

from dataclasses import dataclass

MIN_TRADES_FOR_KELLY = 15  # matches retune.py's MIN_TRADES_FOR_SIGNIFICANCE
KELLY_MULTIPLIER = 0.5  # half-Kelly - a standard, more conservative default
MIN_POSITION_SIZE_PCT = 0.01
MAX_POSITION_SIZE_PCT = 0.15  # never risk more than this, however good the estimate looks


@dataclass
class KellyEstimate:
    trades_used: int
    win_rate: float
    avg_win: float
    avg_loss: float
    full_kelly_fraction: float
    position_size_pct: float  # after applying KELLY_MULTIPLIER and clipping


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """f* = (b*p - q) / b. Returns 0 if the estimated edge is negative
    (Kelly's own answer to a losing setup is "don't bet")."""
    if avg_loss <= 0 or avg_win <= 0:
        return 0.0
    b = avg_win / avg_loss
    p = win_rate
    q = 1 - p
    f = (b * p - q) / b
    return max(f, 0.0)


def compute_position_size_pct(
    closed_trade_pnls: list[float],
    default_pct: float,
    min_trades: int = MIN_TRADES_FOR_KELLY,
    kelly_multiplier: float = KELLY_MULTIPLIER,
) -> KellyEstimate:
    """
    Position size to use for the NEXT trade, from every trade closed SO
    FAR (an expanding, walk-forward window - never trades that haven't
    closed yet, which would be lookahead). Falls back to default_pct
    until there's enough history to trust the estimate.
    """
    if len(closed_trade_pnls) < min_trades:
        return KellyEstimate(
            trades_used=len(closed_trade_pnls), win_rate=0.0, avg_win=0.0, avg_loss=0.0,
            full_kelly_fraction=0.0, position_size_pct=default_pct,
        )

    wins = [p for p in closed_trade_pnls if p > 0]
    losses = [p for p in closed_trade_pnls if p <= 0]

    if not wins or not losses:
        return KellyEstimate(
            trades_used=len(closed_trade_pnls), win_rate=0.0, avg_win=0.0, avg_loss=0.0,
            full_kelly_fraction=0.0, position_size_pct=default_pct,
        )

    win_rate = len(wins) / len(closed_trade_pnls)
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))

    f_full = kelly_fraction(win_rate, avg_win, avg_loss)
    sized = f_full * kelly_multiplier
    clipped = max(MIN_POSITION_SIZE_PCT, min(MAX_POSITION_SIZE_PCT, sized))

    return KellyEstimate(
        trades_used=len(closed_trade_pnls), win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss,
        full_kelly_fraction=f_full, position_size_pct=clipped,
    )
