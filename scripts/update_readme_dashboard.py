"""
Regenerates the README's P&L badges and equity curve chart daily.

Scope, deliberately: equity and total return come straight from the live
Alpaca account (fully robust, no reconstruction needed). Win rate, P&L,
and trades-closed come from docs/trade-ledger.md's table instead of
re-deriving them from raw order fills here - that reconstruction has real
edge cases (see docs/trade-ledger.md's notes on META's staggered 5-day
close), and the ledger is already the place that gets it right. This
script only regenerates local files - it never runs git commands, per
the project's standing rule that commits/pushes are a human's call.
"""

import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.broker import get_account_equity
from src.equity_history import read_equity_history

STARTING_EQUITY = 100_000.0
ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
LEDGER_PATH = ROOT / "docs" / "trade-ledger.md"
CHART_PATH = ROOT / "docs" / "equity_curve.png"

MARKER_START = "<!-- PNL_DASHBOARD_START"
MARKER_END = "<!-- PNL_DASHBOARD_END -->"

# Same palette as docs/dashboard.html (validated via the dataviz skill's
# palette checker) - kept consistent between the static chart and the
# interactive one.
ACCENT = "#2a78d6"
GOOD = "#0ca30c"
GRID = "#e1dfd9"
TEXT = "#52514e"
INK = "#0b0b0b"


def _read_ledger_stats() -> dict:
    """Parse the bolded P&L cell in each trade row of the ledger table."""
    text = LEDGER_PATH.read_text(encoding="utf-8")
    pnls = [float(m.replace(",", "")) for m in re.findall(r"\*\*([+-]\d[\d,]*)\*\*", text.replace("$", ""))]
    wins = [p for p in pnls if p > 0]
    return {
        "trades_closed": len(pnls),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "realized_pnl": sum(pnls),
    }


def _badge_url(label: str, message: str, color: str) -> str:
    def encode(s: str) -> str:
        # "%" must be escaped FIRST - every other substitution below
        # introduces its own "%", and escaping those too would double-
        # encode them (e.g. "+" -> "%2B", then a later "%" -> "%25" pass
        # would mangle that into "%252B").
        return (
            s.replace("%", "%25")
            .replace("+", "%2B").replace("$", "%24")
            .replace(",", "%2C").replace("&", "%26")
            .replace("-", "--").replace(" ", "_")
        )
    return f"https://img.shields.io/badge/{encode(label)}-{encode(message)}-{color}?style=for-the-badge"


def _regenerate_chart(history: list[dict]) -> None:
    dates = [h["date"][5:] for h in history]
    equity = [h["equity"] for h in history]

    fig, ax = plt.subplots(figsize=(9.6, 3.6), dpi=200)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")

    x = range(len(dates))
    ax.plot(x, equity, color=ACCENT, linewidth=2.4, solid_capstyle="round", zorder=3)
    ax.fill_between(x, equity, min(equity) * 0.985, color=ACCENT, alpha=0.12, zorder=2)
    ax.scatter([x[-1]], [equity[-1]], color=GOOD, s=55, zorder=4, edgecolor="#fcfcfb", linewidth=1.5)

    ax.annotate(f"${equity[-1]:,.0f}", (x[-1], equity[-1]), textcoords="offset points", xytext=(-8, 14),
                ha="right", fontsize=13, fontweight="bold", color=INK, family="sans-serif")
    ax.annotate(f"${equity[0]:,.0f}", (x[0], equity[0]), textcoords="offset points", xytext=(0, -22),
                ha="left", fontsize=9.5, color=TEXT, family="sans-serif")

    ax.set_xticks(list(x))
    ax.set_xticklabels(dates, fontsize=8.5, color=TEXT, family="monospace")
    ax.set_ylim(min(equity) * 0.97, max(equity) * 1.05)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", labelsize=8.5, colors=TEXT, length=0)
    ax.tick_params(axis="x", length=0)
    ax.yaxis.set_major_formatter(lambda v, pos: f"${v / 1000:.0f}k")
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=1)

    plt.tight_layout()
    plt.savefig(CHART_PATH, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def main():
    equity = get_account_equity()
    total_return_pct = (equity - STARTING_EQUITY) / STARTING_EQUITY * 100

    ledger_stats = _read_ledger_stats()

    history = read_equity_history()
    if not history:
        print("No equity history yet - skipping chart regeneration.")
        return
    _regenerate_chart(history)

    return_color = GOOD.lstrip("#") if total_return_pct >= 0 else "d03b3b"
    realized_pnl = ledger_stats["realized_pnl"]
    pnl_color = GOOD.lstrip("#") if realized_pnl >= 0 else "d03b3b"
    pnl_message = f"{'+' if realized_pnl >= 0 else '-'}${abs(realized_pnl):,.0f}"

    block = f"""{MARKER_START}: auto-regenerated daily by scripts/update_readme_dashboard.py - do not hand-edit between these markers, it will be overwritten -->
<p align="center">
  <img src="{_badge_url('return', f'{total_return_pct:+.2f}%', return_color)}" alt="Total return {total_return_pct:+.2f}%">
  <img src="{_badge_url('P&L', pnl_message, pnl_color)}" alt="Realized P&L">
  <img src="{_badge_url('win rate', f'{ledger_stats["win_rate"]:.0%}', ACCENT.lstrip('#'))}" alt="Win rate {ledger_stats['win_rate']:.0%}">
  <img src="{_badge_url('trades closed', str(ledger_stats['trades_closed']), ACCENT.lstrip('#'))}" alt="{ledger_stats['trades_closed']} trades closed">
  <img src="{_badge_url('status', 'autonomous', GOOD.lstrip('#'))}" alt="Status: autonomous">
</p>

<p align="center">
  <img src="docs/equity_curve.png" alt="AlphaCa account equity over time, currently ${equity:,.0f}" width="800">
</p>
{MARKER_END}"""

    readme = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), re.DOTALL)
    if not pattern.search(readme):
        print("README markers not found - skipping README update (chart was still regenerated).")
        return
    README_PATH.write_text(pattern.sub(block, readme), encoding="utf-8")
    print(f"Updated README dashboard: equity=${equity:,.2f}, return={total_return_pct:+.2f}%")


if __name__ == "__main__":
    main()
