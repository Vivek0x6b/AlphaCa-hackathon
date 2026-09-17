# Incident log

A running, honest record of real bugs found running AlphaCa live against a
real (paper) account — not a curated highlight reel. Kept because "we
found and fixed real problems in production" is itself part of the
evidence this project is built on, and because reconstructing this from
memory later would be worse than writing it down now.

## 2026-09-03/04 — per-ticker scan failures took down the whole day

A single ticker's transient API error (rate limit / network hiccup) inside
the signal-scan or exit-check loop was uncaught, aborting the entire run —
one bad tick meant every ticker after it in the watchlist went unchecked
for the day. Fixed by wrapping each ticker's processing in its own
try/except so one failure doesn't blind the agent to the rest of the
watchlist.

## 2026-09-04 — closing a spread raced its own fill

`close_debit_spread()` closed the short leg then, after a fixed 2-second
sleep, closed the long leg — assuming the short leg would have filled by
then. It doesn't always: the short position still legitimately exists (and
covers the long) until its close order fills, so closing the long leg too
early got rejected as an uncovered position. Also found: the exit-check
code marked a spread as fully closed and stopped tracking it whenever
`should_exit` was true, without checking whether the close actually
completed — a spread that only got the short leg closed was reported as
"closed both legs" while the long leg sat open and untracked. Fixed by
polling for the short leg's actual fill before submitting the long leg's
close, and only dropping tracking when the close function confirms both
legs went out.

## 2026-09-04/06 — stale close orders never got repriced

A close order that didn't fill right away just sat at its original quoted
price indefinitely, retried every day with the same stale price, always
failing with "insufficient qty" (a fresh order attempt colliding with the
already-resting stale one). Fixed by always cancelling any resting order
on a leg and resubmitting fresh at the current quote on every retry.

## 2026-09-09 — the scheduler process silently died on a Windows Update reboot

The live agent ran as a permanently-running Python process (`scheduler.py`,
sleep-loop). A machine reboot (Windows Update) killed it with nothing to
restart it and no alert that it had died — the system went completely dark
for a day before this was noticed by manual inspection. Replaced with a
Windows Scheduled Task (`daily_run.py`, single-shot) that the OS itself
restarts across reboots, and which also fixed a second, unrelated problem:
a long-running process holds every module in memory from whenever it
started, so a code fix made hours later had zero effect until someone
remembered to manually restart it. A fresh process per run has neither
failure mode.

## 2026-09-09/10 — duplicate entries from a race during a manual re-test

While manually re-triggering the (at the time, brand new) Scheduled Task
twice in one evening to add log capture, the second run's exit-check
mistook the first run's still-unfilled entry orders for "already fully
closed" (zero matching positions either way looked identical without a
pending-order check) and dropped their tracking — freeing AMD and META up
to be entered a second time each, at slightly different strikes, while the
first orders were still resting. Both duplicate sets filled. Fixed with
`has_pending_order()` so an unfilled order is never confused with a closed
position, plus a broader belt-and-suspenders guard: an entry is now
blocked if a ticker has *any* option exposure at all, not just a clean
matched pair.

## 2026-09-10/12 — the duplicate positions broke exit math and closing

With two different long strikes and two different short strikes open on
one ticker, the exit-check code (which assumed exactly one long + one
short per ticker) silently used an arbitrary single leg's price for P&L
(wrong percentage) and an arbitrary single short leg's quantity when
closing (AMD's stop-loss close failed with "requested 10, available 5" —
the long leg's combined quantity against one short leg's actual quantity).
Fixed by generalizing exit-check and closing to aggregate and close
however many legs actually exist per ticker, instead of assuming exactly
one of each.

## 2026-09-08 to ~09-11 — the autonomous re-tune job could never adopt anything

Its significance test paired baseline and candidate trades by list
position, requiring equal trade counts. Almost any parameter change shifts
total trade count somewhere in a 2-year backtest (an exit-timing change
ripples into which later signals get taken under the position limit), so
the comparison silently fell back to "no difference" on nearly every
check, every day, since the job started — not because nothing helped, but
because the comparison mechanism gave up whenever counts differed, which
was nearly always. Fixed by pairing trades by (ticker, entry_date) instead
of position; it immediately found and adopted a real, significant
improvement (narrowing the short leg's delta range) on the first run after
the fix.

## 2026-09-14 — DAY close orders were silently expiring at market open, never attempting to fill

The daily cycle runs at 4:15pm ET, right after the close — so any close
order placed then has essentially no window to fill during that session.
Confirmed live: it also does *not* get a fresh shot at the next session's
open. It just expires exactly at the session boundary without ever
attempting to trade, over and over, every day it doesn't happen to be
manually intervened on during real market hours. This let AMD's stop-loss
(meant to cap the loss at -40%) sit un-executed for days while the
position kept falling, eventually closing at -67% once discovered and
fixed manually. Fixed by switching close orders from `DAY` to `GTC`, so
they actually rest through the next session instead of vanishing at the
boundary.

## 2026-09-16 — crypto strategy evaluated and dropped for lack of a real edge

Explored a parallel spot-crypto strategy (BTC/ETH/SOL, later widened to 13
liquid coins) reusing the equity signal logic, backtested against real
Alpaca crypto history (unlike options, no Black-Scholes approximation
needed). The equity strategy's 20-day breakout window fired constantly on
crypto's daily noise and was barely breakeven with a 15% win rate, mostly
losing within hours to `thesis_invalidated`. Tried several genuinely
different angles: separating fast (profit/stop) from slow (thesis
invalidation) exit checks, an invalidation buffer, much longer breakout
windows (55-100 days), a wider watchlist for a bigger sample, and a sweep
of profit-target/stop-loss ratios. Every variant capped out near
breakeven or worse - no configuration showed a real, demonstrated edge the
way every equity-side change did. Along the way, also found and fixed a
real bug: `evaluate_signal()` read its parameters from hardcoded
`config.watchlist` globals instead of accepting overrides, so an early
parameter sweep silently tested the same values five times. Decision:
dropped the crypto strategy rather than ship something unproven, and
redirected the actual underlying want (fast exits, not waiting for a
once-daily check) into the equity strategy instead - see the intraday
exit-check entry below.

## 2026-09-16 — exits only ran once daily, missing intraday profit-taking

The exit-check logic (profit target, stop loss, thesis invalidation) only
ever ran once a day, at 4:15pm ET alongside the entry scan - even though
it's based on Alpaca's live position marks and has no actual dependency on
waiting for the day's close. A position that hit its profit target at
11am sat open and unclosed for hours, giving back gains, purely because
nothing checked it sooner. Added `scripts/intraday_exit_check.py` and a
second Windows Scheduled Task running it every 15 minutes during market
hours - entry signals stay on the once-daily cycle (the breakout/trend
logic is genuinely defined against a confirmed daily close), but exits no
longer wait on that same clock.

## 2026-09-15 — added a health check instead of relying on catching things by hand

Every incident above was found by manually checking positions, orders, and
the journal by hand — the system had no way to flag its own anomalies.
Added `scripts/health_check.py`, run at the end of every daily cycle:
confirms the scheduled task's last run actually succeeded, cross-checks
locally tracked trades against Alpaca's real account state for drift, and
surfaces any unresolved `ticker_error` or `partial_close` entries from that
day's journal.
