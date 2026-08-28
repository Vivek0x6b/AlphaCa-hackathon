"""
Backtest the momentum breakout debit-spread strategy against real historical
stock prices, using Black-Scholes to approximate option pricing (Alpaca has
no historical option chain/Greeks data to replay). See
docs/designs/autonomous-backtest-retuning.md for the full design and the
assumptions this approximation makes.

Reuses the exact same pure-logic modules the live agent uses (signals.py,
options_selector.py, execution.py, position_manager.py) unchanged, so this
tests the real strategy, not a parallel reimplementation of it.

Two conservative corrections applied on top of the raw Black-Scholes prices,
so this doesn't look artificially better than live trading would:
  1. Volatility input is trailing realized vol, not real IV (real IV usually
     runs a bit higher, so this slightly underprices options).
  2. A slippage haircut is applied on entry/exit (pay more on the ask side,
     receive less on the bid side) to approximate the bid-ask spread cost
     that a theoretical Black-Scholes price doesn't include.
"""

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.watchlist import (
    WATCHLIST,
    MIN_DAYS_TO_EXPIRY,
    MAX_DAYS_TO_EXPIRY,
    BREAKOUT_LOOKBACK_DAYS,
    TREND_MA_DAYS,
    PUT_TRADING_ENABLED,
)
from src.market_data import fetch_bars
from src.signals import evaluate_signal
from src.options_selector import select_debit_spread
from src.execution import size_position
from src.position_manager import evaluate_exit
from src.black_scholes import bs_price, compute_realized_volatility, synthesize_option_chain

STARTING_EQUITY = 100_000.0
BACKTEST_LOOKBACK_DAYS = 730  # ~2 years of history

# Conservative slippage haircut applied to theoretical Black-Scholes prices,
# so the backtest doesn't assume a perfect fill at the theoretical mid.
SLIPPAGE_PCT = 0.02


@dataclass
class BacktestPosition:
    ticker: str
    direction: str
    long_strike: float
    short_strike: float
    expiry: date
    contracts: int
    entry_debit_per_contract: float
    entry_date: date
    breakout_level: float


@dataclass
class ClosedTrade:
    ticker: str
    direction: str
    entry_date: date
    exit_date: date
    contracts: int
    entry_debit_per_contract: float
    exit_value_per_contract: float
    pnl: float
    reason: str


def _leg_price(underlying_price, strike, years_left, volatility, direction, side):
    """Black-Scholes price for one leg, haircut for the side of the market
    we'd actually trade on (buying pays more, selling receives less)."""
    price = bs_price(underlying_price, strike, years_left, volatility, direction)
    if side == "buy":
        return price * (1 + SLIPPAGE_PCT)
    return price * (1 - SLIPPAGE_PCT)


def run_backtest(
    bars_by_ticker: dict | None = None,
    long_leg_delta_range: tuple[float, float] = None,
    short_leg_delta_range: tuple[float, float] = None,
    min_days_to_expiry: int = MIN_DAYS_TO_EXPIRY,
    max_days_to_expiry: int = MAX_DAYS_TO_EXPIRY,
    profit_target_pct: float = None,
    stop_loss_pct: float = None,
):
    """
    Run the backtest. Parameters default to the live strategy's config
    values; pass overrides (and a pre-fetched bars_by_ticker, to avoid
    re-fetching from Alpaca) to sweep parameters cheaply.
    """
    from config.watchlist import LONG_LEG_DELTA_RANGE, SHORT_LEG_DELTA_RANGE
    from src.position_manager import PROFIT_TARGET_PCT, STOP_LOSS_PCT

    if long_leg_delta_range is None:
        long_leg_delta_range = LONG_LEG_DELTA_RANGE
    if short_leg_delta_range is None:
        short_leg_delta_range = SHORT_LEG_DELTA_RANGE
    if profit_target_pct is None:
        profit_target_pct = PROFIT_TARGET_PCT
    if stop_loss_pct is None:
        stop_loss_pct = STOP_LOSS_PCT

    if bars_by_ticker is None:
        print(f"Fetching {BACKTEST_LOOKBACK_DAYS} days of history for {len(WATCHLIST)} tickers...")
        bars_by_ticker = fetch_bars(WATCHLIST, lookback_days=BACKTEST_LOOKBACK_DAYS)

    vol_by_ticker = {
        ticker: compute_realized_volatility(df["close"]) for ticker, df in bars_by_ticker.items()
    }

    # SPY is virtually guaranteed to have a full trading calendar; use its
    # dates as the reference timeline all tickers are checked against.
    reference_ticker = "SPY" if "SPY" in bars_by_ticker else next(iter(bars_by_ticker))
    all_dates = [ts.date() for ts in bars_by_ticker[reference_ticker].index]

    min_rows = max(BREAKOUT_LOOKBACK_DAYS, TREND_MA_DAYS) + 1

    equity = STARTING_EQUITY
    open_positions: list[BacktestPosition] = []
    closed_trades: list[ClosedTrade] = []

    for i, current_date in enumerate(all_dates):
        if i < min_rows:
            continue

        # --- Check exits on everything currently open ---
        still_open = []
        for pos in open_positions:
            if current_date not in bars_by_ticker[pos.ticker].index.date:
                still_open.append(pos)
                continue

            current_price = float(
                bars_by_ticker[pos.ticker].loc[
                    bars_by_ticker[pos.ticker].index.date == current_date, "close"
                ].iloc[0]
            )
            days_left = (pos.expiry - current_date).days
            years_left = max(days_left, 0) / 365.25
            vol = vol_by_ticker[pos.ticker].loc[
                vol_by_ticker[pos.ticker].index.date == current_date
            ]
            vol = float(vol.iloc[0]) if len(vol) and not pd.isna(vol.iloc[0]) else 0.20

            long_price = bs_price(current_price, pos.long_strike, years_left, vol, pos.direction)
            short_price = bs_price(current_price, pos.short_strike, years_left, vol, pos.direction)
            current_value = (long_price - short_price) * 100

            if days_left <= 0:
                should_exit, reason = True, "expired"
            else:
                decision = evaluate_exit(
                    ticker=pos.ticker,
                    direction=pos.direction,
                    entry_debit=pos.entry_debit_per_contract,
                    current_value=current_value,
                    current_price=current_price,
                    breakout_level=pos.breakout_level,
                    profit_target_pct=profit_target_pct,
                    stop_loss_pct=stop_loss_pct,
                )
                should_exit, reason = decision.should_exit, decision.reason

            if should_exit:
                pnl = (current_value - pos.entry_debit_per_contract) * pos.contracts
                equity += pnl
                closed_trades.append(
                    ClosedTrade(
                        ticker=pos.ticker,
                        direction=pos.direction,
                        entry_date=pos.entry_date,
                        exit_date=current_date,
                        contracts=pos.contracts,
                        entry_debit_per_contract=pos.entry_debit_per_contract,
                        exit_value_per_contract=current_value,
                        pnl=pnl,
                        reason=reason,
                    )
                )
            else:
                still_open.append(pos)
        open_positions = still_open

        # --- Check for new signals ---
        for ticker in WATCHLIST:
            if ticker not in bars_by_ticker:
                continue
            df = bars_by_ticker[ticker]
            if current_date not in df.index.date:
                continue

            bars_so_far = df.loc[df.index.date <= current_date]
            if len(bars_so_far) < min_rows:
                continue

            result = evaluate_signal(ticker, bars_so_far)
            if not result.fired:
                continue
            if result.direction == "put" and not PUT_TRADING_ENABLED:
                continue

            vol = vol_by_ticker[ticker].loc[vol_by_ticker[ticker].index.date == current_date]
            vol = float(vol.iloc[0]) if len(vol) and not pd.isna(vol.iloc[0]) else 0.20

            chain = synthesize_option_chain(
                underlying_price=result.close,
                as_of_date=current_date,
                direction=result.direction,
                volatility=vol,
                min_days_to_expiry=min_days_to_expiry,
                max_days_to_expiry=max_days_to_expiry,
            )
            spread = select_debit_spread(
                ticker,
                result.direction,
                chain,
                as_of_date=current_date,
                long_leg_delta_range=long_leg_delta_range,
                short_leg_delta_range=short_leg_delta_range,
                min_days_to_expiry=min_days_to_expiry,
                max_days_to_expiry=max_days_to_expiry,
            )
            if spread is None:
                continue

            long_days_left = (spread.expiry - current_date).days
            years_left = long_days_left / 365.25
            long_price = _leg_price(
                result.close, spread.long_leg.strike, years_left, vol, result.direction, "buy"
            )
            short_price = _leg_price(
                result.close, spread.short_leg.strike, years_left, vol, result.direction, "sell"
            )

            open_count = len(open_positions)
            plan = size_position(
                spread,
                account_equity=equity,
                long_leg_price=long_price,
                short_leg_price=short_price,
                open_position_count=open_count,
            )
            if plan is None:
                continue

            open_positions.append(
                BacktestPosition(
                    ticker=ticker,
                    direction=result.direction,
                    long_strike=spread.long_leg.strike,
                    short_strike=spread.short_leg.strike,
                    expiry=spread.expiry,
                    contracts=plan.contracts,
                    entry_debit_per_contract=plan.est_cost_per_contract,
                    entry_date=current_date,
                    breakout_level=result.breakout_level,
                )
            )

    return equity, closed_trades, open_positions


def print_summary(final_equity, closed_trades, open_positions):
    print(f"\n{'=' * 60}")
    print("BACKTEST SUMMARY")
    print(f"{'=' * 60}")
    print(f"Starting equity: ${STARTING_EQUITY:,.2f}")
    print(f"Final equity (realized only): ${final_equity:,.2f}")
    total_return_pct = (final_equity - STARTING_EQUITY) / STARTING_EQUITY
    print(f"Total return: {total_return_pct:+.2%}")
    print(f"Closed trades: {len(closed_trades)}")
    print(f"Still open at end of period: {len(open_positions)}")

    if closed_trades:
        wins = [t for t in closed_trades if t.pnl > 0]
        losses = [t for t in closed_trades if t.pnl <= 0]
        print(f"Win rate: {len(wins) / len(closed_trades):.1%} ({len(wins)}W / {len(losses)}L)")
        if wins:
            print(f"Avg win: ${sum(t.pnl for t in wins) / len(wins):,.2f}")
        if losses:
            print(f"Avg loss: ${sum(t.pnl for t in losses) / len(losses):,.2f}")

        print("\nTrade log:")
        for t in closed_trades:
            print(
                f"  [{t.ticker} {t.direction}] {t.entry_date} -> {t.exit_date} "
                f"({t.reason}): {t.contracts} contract(s), P&L ${t.pnl:,.2f}"
            )
    else:
        print("\nNo trades fired during the backtest period.")


if __name__ == "__main__":
    final_equity, closed_trades, open_positions = run_backtest()
    print_summary(final_equity, closed_trades, open_positions)
