# Trade ledger

Every real position AlphaCa has taken on the live (paper) account, reconstructed
from Alpaca's actual fill history — the authoritative source, not the decision
journal, since several incidents this month (duplicate entries, delayed closes)
mean the journal alone doesn't always match what actually executed. P&L is net
of all legs' actual fills. Updated as positions close.

| Ticker | Direction | Entered | Contracts | Entry cost | Exited | Exit reason | Realized P&L |
|---|---|---|---|---|---|---|---|
| AAPL | call | 2026-09-02 | 3 | $1,719 | 2026-09-09 | stop_loss (delayed — see note) | **-$1,137** (-66%) |
| TSLA | call | 2026-09-04 | 2 | $960 | 2026-09-09 | stop_loss | **-$50** (-5%) |
| AMD #1 | call | 2026-09-10 | 10 (5+5, duplicated) | $12,780 | 2026-09-15 | stop_loss (delayed — see note) | **-$9,280** (-73%) |
| META | call | 2026-09-10 | 8 (4+4, duplicated) | $14,364 | 2026-09-21 (staggered close over 5 days) | profit_target | **+$18,652** (+130%) |
| AMD #2 | call | 2026-09-18 | 4 | $6,552 | 2026-09-21 | profit_target | **+$7,168** (+109%) |

**Running total (5 closed positions): +$15,353.** Consistent with the account's
actual equity change over the same period (started ~$100,000, now $115,348.38).

## Notes on accuracy

- **AAPL and AMD #1's losses are worse than the -40% stop-loss threshold
  implies** (realized at -66% and -73%). Both got caught by the DAY-order-
  expiry bug (see incident log, 2026-09-14 entry): the stop-loss *decision*
  fired on time, but the *close order* silently failed to execute for
  multiple days while the position kept falling, before the fix (GTC orders)
  landed. This is the single most expensive bug found this month,
  quantifiable directly in these two numbers.
- **AMD #1 and META were each entered twice** by the 2026-09-09/10
  duplicate-entry bug (see incident log) — the contract counts and entry
  costs above reflect the actual doubled position, not the intended single
  entry.
- **META's close took 5 days** (2026-09-16 through 2026-09-21) because its
  two short legs and two long legs closed in separate steps as each cleared
  the multi-leg safety ordering (shorts before longs, one leg's fill
  confirmed before the next order goes out) - not a bug, the same
  deliberately conservative sequencing documented in `src/broker.py`.
  The position was in profit target range for days before the close fully
  completed, which is why the eventual realized gain (+130%) is well above
  the +50% target - it kept running while the close mechanically worked
  through both duplicated entries' legs.
- **TSLA's near-breakeven loss** (-5% despite hitting a "stop_loss" reason)
  is real, unaffected by either bug — a case where the underlying moved
  back toward entry between the decision and the fill.
- **AMD #2** was a clean, single entry (the duplicate-entry guard added
  after AMD #1/META's incident worked correctly here) and a clean same-day
  profit-target close - the first position with zero incidents anywhere in
  its lifecycle.
