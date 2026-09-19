# Trade ledger

Every real position AlphaCa has taken on the live (paper) account, reconstructed
from Alpaca's actual fill history — the authoritative source, not the decision
journal, since this week's incidents (duplicate entries, delayed closes) mean
the journal alone doesn't always match what actually executed. P&L is net of
both legs' actual fills. Updated as positions close.

| Ticker | Direction | Entered | Contracts | Entry cost | Exited | Exit reason | Realized P&L |
|---|---|---|---|---|---|---|---|
| AAPL | call | 2026-09-02 | 3 | $1,719 | 2026-09-09 | stop_loss (delayed — see note) | **-$1,137** (-66%) |
| TSLA | call | 2026-09-04 | 2 | $960 | 2026-09-09 | stop_loss | **-$50** (-5%) |
| AMD | call | 2026-09-10 | 10 (5+5, duplicated) | $12,780 | 2026-09-15 | stop_loss (delayed — see note) | **-$9,280** (-73%) |
| META | call | 2026-09-10 | 8 (4+4, duplicated) | $14,364 | closing (2026-09-17, in progress) | profit_target | ~**+$7,300** (+51%, unrealized as of hit) |

**Running total (3 fully closed positions): -$10,467.** META's close will add a
real win once it fully settles — see `docs/incident-log.md` for why closes
sometimes take more than one day to fully execute this week.

## Notes on accuracy

- **AAPL and AMD's losses are worse than the stop-loss threshold implies**
  (-40% target, but realized at -66% and -73%). Both got caught by the
  DAY-order-expiry bug (see incident log, 2026-09-14 entry): the stop-loss
  *decision* fired on time, but the *close order* silently failed to execute
  for multiple days while the position kept falling, before the fix (GTC
  orders) landed. This is the single most expensive bug found this week,
  quantifiable directly in these two numbers.
- **AMD and META were each entered twice** by the 2026-09-09/10
  duplicate-entry bug (see incident log) — the contract counts and entry
  costs above reflect the actual doubled position, not the intended single
  entry. AMD's intended single-entry loss would have been roughly half this
  size at the correct -40% stop, not -73%.
- TSLA's near-breakeven loss (-5% despite hitting a "stop_loss" reason) is
  real, unaffected by either bug — a case where the underlying moved back
  toward entry between the decision and the fill.
