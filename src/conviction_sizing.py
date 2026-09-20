"""
Signal-strength (conviction) position sizing: scale size continuously by
how strong a fired signal's confirmation is, instead of one flat size for
every trade.

Grounded in evidence found in this project's own testing (see docs/
strategy-scorecard.md's entry-cap experiment): ranking same-day fired
signals by relative volume and taking only the strongest one produced a
real, non-arbitrary improvement over an arbitrary tie-break (the ranking
criterion carried genuine information). That experiment's overall
adoption was rejected for being fragile to the specific entry-count cap
chosen - but the underlying signal (relative volume distinguishes
stronger from weaker confirmations) is still real. This applies that same
information continuously to SIZE instead of using it as a hard gate on
WHICH trades get taken, deliberately avoiding the cliff-edge mechanism
that made the earlier version fragile.
"""

BASELINE_RELATIVE_VOLUME = 1.2  # the signal's own minimum confirmation threshold


def conviction_multiplier(
    relative_volume: float | None,
    min_multiplier: float = 0.75,
    max_multiplier: float = 1.5,
    reference_relative_volume: float = 2.5,
) -> float:
    """
    1.0x at BASELINE_RELATIVE_VOLUME (the bare minimum that qualifies a
    signal at all), scaling linearly up to max_multiplier at
    reference_relative_volume and beyond (clipped, so one outlier
    confirmation doesn't produce an unbounded size), and down to
    min_multiplier for confirmations right at the bare minimum threshold.
    None (missing data) returns 1.0x, i.e. no adjustment.
    """
    if relative_volume is None:
        return 1.0

    if relative_volume <= BASELINE_RELATIVE_VOLUME:
        return min_multiplier

    span = reference_relative_volume - BASELINE_RELATIVE_VOLUME
    progress = (relative_volume - BASELINE_RELATIVE_VOLUME) / span
    progress = max(0.0, min(1.0, progress))

    return min_multiplier + (max_multiplier - min_multiplier) * progress
