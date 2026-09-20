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
from src.market_regime import market_regime_ok, MARKET_REGIME_MA_DAYS
from src.composite_signal import evaluate_composite_signal
from src.atr_stops import compute_atr, compute_baseline_atr_pct, adaptive_thresholds, ATR_WINDOW
from src.kelly_sizing import compute_position_size_pct
from src.vol_spike_veto import vol_spike_veto, SPIKE_THRESHOLD
from src.market_vol_sizing import compute_market_vol_percentile, market_vol_size_multiplier
from src.circuit_breaker_sizing import circuit_breaker_multiplier
from src.conviction_sizing import conviction_multiplier

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
    peak_value: float
    # Per-position exit thresholds - normally the same fixed value for
    # every position (profit_target_pct/stop_loss_pct), but when ATR
    # adaptive stops are enabled these are set once at entry (scaled to
    # that ticker's volatility then) and used for every exit check on
    # this specific position, not recomputed as volatility changes later.
    profit_target_pct: float
    stop_loss_pct: float


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


def _check_exit_hourly(
    pos, hourly_df, current_date, years_left, vol, use_naked_long,
    trailing_stop_pct,
):
    """
    Check exit conditions at each hour within one day, Black-Scholes
    repriced from that hour's underlying close - not just once at the
    day's final close. Mutates pos.peak_value as it goes (same as the
    once-daily path). Returns (should_exit, reason, last_price, last_value)
    - "last" is whichever hour triggered the exit, or the day's final
    hour if none did. Uses pos.profit_target_pct/pos.stop_loss_pct - the
    per-position thresholds set at entry (see BacktestPosition), which are
    the fixed global values unless ATR-adaptive stops are enabled.
    """
    day_hours = hourly_df.loc[hourly_df.index.date == current_date]

    last_price = last_value = None
    for _, bar in day_hours.iterrows():
        price = float(bar["close"])
        long_price = bs_price(price, pos.long_strike, years_left, vol, pos.direction)
        short_price = 0.0 if use_naked_long else bs_price(price, pos.short_strike, years_left, vol, pos.direction)
        value = (long_price - short_price) * 100

        decision = evaluate_exit(
            ticker=pos.ticker,
            direction=pos.direction,
            entry_debit=pos.entry_debit_per_contract,
            current_value=value,
            current_price=price,
            breakout_level=pos.breakout_level,
            profit_target_pct=pos.profit_target_pct,
            stop_loss_pct=pos.stop_loss_pct,
            peak_value=pos.peak_value,
            trailing_stop_pct=trailing_stop_pct,
        )
        pos.peak_value = decision.peak_value
        last_price, last_value = price, value

        if decision.should_exit:
            return True, decision.reason, price, value

    return False, None, last_price, last_value


def run_backtest(
    bars_by_ticker: dict | None = None,
    long_leg_delta_range: tuple[float, float] = None,
    short_leg_delta_range: tuple[float, float] = None,
    min_days_to_expiry: int = MIN_DAYS_TO_EXPIRY,
    max_days_to_expiry: int = MAX_DAYS_TO_EXPIRY,
    profit_target_pct: float = None,
    stop_loss_pct: float = None,
    trailing_stop_pct: float | None = None,
    use_regime_filter: bool = False,
    regime_ma_days: int = MARKET_REGIME_MA_DAYS,
    use_naked_long: bool = False,
    hourly_bars_by_ticker: dict | None = None,
    composite_weights: dict | None = None,
    composite_threshold: float = 1.0,
    use_atr_adaptive_stops: bool = False,
    atr_min_scale: float = 0.5,
    atr_max_scale: float = 2.0,
    use_kelly_sizing: bool = False,
    use_vol_spike_veto: bool = False,
    vol_spike_threshold: float = SPIKE_THRESHOLD,
    max_new_entries_per_day: int | None = None,
    use_market_vol_sizing: bool = False,
    market_vol_min_multiplier: float = 0.5,
    market_vol_max_multiplier: float = 1.5,
    use_circuit_breaker: bool = False,
    circuit_breaker_loss_streak: int = 2,
    circuit_breaker_reduction: float = 0.25,
    use_conviction_sizing: bool = False,
    conviction_min_multiplier: float = 0.75,
    conviction_max_multiplier: float = 1.5,
):
    """
    Run the backtest. Parameters default to the live strategy's config
    values; pass overrides (and a pre-fetched bars_by_ticker, to avoid
    re-fetching from Alpaca) to sweep parameters cheaply.

    trailing_stop_pct: None (default) keeps the original flat profit
        target. Set to test letting winners run past profit_target_pct
        and only closing on a pullback from the peak (see
        position_manager.evaluate_exit).
    use_regime_filter: if True, only take call signals when SPY is above
        its own 200-day average (src/market_regime.py). Puts are
        unaffected (disabled separately via PUT_TRADING_ENABLED anyway).
    use_naked_long: if True, skip the short leg entirely - buy just the
        long call. Mathematically identical to a debit spread whose
        short leg is worth $0, so this is implemented by zeroing out the
        short leg's price everywhere rather than a separate code path:
        costs more per contract (no premium collected from selling the
        short leg) and gives up the spread's built-in defined-risk cap,
        but removes the short strike's cap on upside.
    hourly_bars_by_ticker: if given, exits are checked against each
        hour's underlying price within the day (Black-Scholes repriced),
        not just once at the daily close - matches how the live system
        actually checks exits now (every 15 minutes during market hours,
        see scripts/intraday_exit_check.py). A daily-only backtest
        understates how fast an intraday move can be caught (or, for a
        trailing stop, overstates how far price can move between checks)
        - this is what let an earlier trailing-stop test look far worse
        than it behaves under real, frequent checking. None (default)
        keeps the original once-daily check.
    composite_weights: if given (from scripts/factor_research.py's IC
        output via src/composite_signal.weights_from_ic), entry signals
        come from the IC-weighted multi-factor composite score instead of
        the single binary breakout condition. None (default) keeps the
        original signals.py breakout/trend/volume logic unchanged.
    use_atr_adaptive_stops: if True, each position's profit_target_pct/
        stop_loss_pct are scaled at entry to that ticker's own recent
        volatility (ATR) relative to the watchlist's typical volatility
        (src/atr_stops.py), instead of every ticker using the same fixed
        thresholds. False (default) keeps the original fixed thresholds.
    """
    from config.watchlist import LONG_LEG_DELTA_RANGE, SHORT_LEG_DELTA_RANGE, POSITION_SIZE_PCT
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

    if use_atr_adaptive_stops:
        atr_pct_by_ticker = {ticker: compute_atr(df, ATR_WINDOW) for ticker, df in bars_by_ticker.items()}
        baseline_atr_pct = compute_baseline_atr_pct(bars_by_ticker, ATR_WINDOW)

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

            days_left = (pos.expiry - current_date).days
            years_left = max(days_left, 0) / 365.25
            vol = vol_by_ticker[pos.ticker].loc[
                vol_by_ticker[pos.ticker].index.date == current_date
            ]
            vol = float(vol.iloc[0]) if len(vol) and not pd.isna(vol.iloc[0]) else 0.20

            if days_left <= 0:
                current_price = float(
                    bars_by_ticker[pos.ticker].loc[
                        bars_by_ticker[pos.ticker].index.date == current_date, "close"
                    ].iloc[0]
                )
                long_price = bs_price(current_price, pos.long_strike, years_left, vol, pos.direction)
                short_price = 0.0 if use_naked_long else bs_price(current_price, pos.short_strike, years_left, vol, pos.direction)
                current_value = (long_price - short_price) * 100
                should_exit, reason = True, "expired"
            elif (
                hourly_bars_by_ticker
                and pos.ticker in hourly_bars_by_ticker
                and current_date in hourly_bars_by_ticker[pos.ticker].index.date
            ):
                should_exit, reason, current_price, current_value = _check_exit_hourly(
                    pos, hourly_bars_by_ticker[pos.ticker], current_date, years_left, vol,
                    use_naked_long, trailing_stop_pct,
                )
            else:
                current_price = float(
                    bars_by_ticker[pos.ticker].loc[
                        bars_by_ticker[pos.ticker].index.date == current_date, "close"
                    ].iloc[0]
                )
                long_price = bs_price(current_price, pos.long_strike, years_left, vol, pos.direction)
                short_price = 0.0 if use_naked_long else bs_price(current_price, pos.short_strike, years_left, vol, pos.direction)
                current_value = (long_price - short_price) * 100
                decision = evaluate_exit(
                    ticker=pos.ticker,
                    direction=pos.direction,
                    entry_debit=pos.entry_debit_per_contract,
                    current_value=current_value,
                    current_price=current_price,
                    breakout_level=pos.breakout_level,
                    profit_target_pct=pos.profit_target_pct,
                    stop_loss_pct=pos.stop_loss_pct,
                    peak_value=pos.peak_value,
                    trailing_stop_pct=trailing_stop_pct,
                )
                should_exit, reason = decision.should_exit, decision.reason
                pos.peak_value = decision.peak_value

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
        # max_new_entries_per_day: when the watchlist's correlated tech
        # names all break out on the same day (one shared macro move, not
        # independent bets), taking every one of them up to the position
        # limit stacks correlated risk rather than diversifying it. This
        # pre-pass ranks same-day candidates by relative volume (the
        # strongest, most-confirmed move) and only lets the top N proceed
        # - a lightweight duplicate of the filtering below, since the cap
        # needs to see every candidate before deciding, not decide
        # ticker-by-ticker as the original loop does.
        allowed_tickers_today = None
        if max_new_entries_per_day is not None:
            candidates = []
            for ticker in WATCHLIST:
                if ticker not in bars_by_ticker or current_date not in bars_by_ticker[ticker].index.date:
                    continue
                bars_so_far = bars_by_ticker[ticker].loc[bars_by_ticker[ticker].index.date <= current_date]
                if len(bars_so_far) < min_rows:
                    continue
                if composite_weights is not None:
                    result = evaluate_composite_signal(ticker, bars_so_far, composite_weights, threshold=composite_threshold)
                else:
                    result = evaluate_signal(ticker, bars_so_far)
                if not result.fired:
                    continue
                if result.direction == "put" and not PUT_TRADING_ENABLED:
                    continue
                if use_regime_filter and result.direction == "call":
                    spy_bars_so_far = bars_by_ticker["SPY"].loc[bars_by_ticker["SPY"].index.date <= current_date]
                    if not market_regime_ok(spy_bars_so_far, ma_days=regime_ma_days):
                        continue
                if use_vol_spike_veto:
                    vetoed, _ = vol_spike_veto(bars_so_far, threshold=vol_spike_threshold)
                    if vetoed:
                        continue
                candidates.append((ticker, result.relative_volume or 0.0))
            candidates.sort(key=lambda x: x[1], reverse=True)
            allowed_tickers_today = {t for t, _ in candidates[:max_new_entries_per_day]}

        for ticker in WATCHLIST:
            if allowed_tickers_today is not None and ticker not in allowed_tickers_today:
                continue
            if ticker not in bars_by_ticker:
                continue
            df = bars_by_ticker[ticker]
            if current_date not in df.index.date:
                continue

            bars_so_far = df.loc[df.index.date <= current_date]
            if len(bars_so_far) < min_rows:
                continue

            if composite_weights is not None:
                result = evaluate_composite_signal(ticker, bars_so_far, composite_weights, threshold=composite_threshold)
            else:
                result = evaluate_signal(ticker, bars_so_far)
            if not result.fired:
                continue
            if result.direction == "put" and not PUT_TRADING_ENABLED:
                continue
            if use_regime_filter and result.direction == "call":
                spy_bars_so_far = bars_by_ticker["SPY"].loc[bars_by_ticker["SPY"].index.date <= current_date]
                if not market_regime_ok(spy_bars_so_far, ma_days=regime_ma_days):
                    continue
            if use_vol_spike_veto:
                vetoed, _ = vol_spike_veto(bars_so_far, threshold=vol_spike_threshold)
                if vetoed:
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
            short_price = 0.0 if use_naked_long else _leg_price(
                result.close, spread.short_leg.strike, years_left, vol, result.direction, "sell"
            )

            open_count = len(open_positions)

            if use_kelly_sizing:
                # Expanding, walk-forward window - only trades closed
                # BEFORE this point in the loop, so this can never see a
                # future trade's outcome (closed_trades is built up in
                # temporal order by the exit-check loop above, earlier
                # each iteration than this entry loop runs).
                kelly = compute_position_size_pct(
                    [t.pnl for t in closed_trades], default_pct=POSITION_SIZE_PCT,
                )
                position_size_pct = kelly.position_size_pct
            else:
                position_size_pct = POSITION_SIZE_PCT

            if use_market_vol_sizing and "SPY" in bars_by_ticker:
                spy_bars_so_far = bars_by_ticker["SPY"].loc[bars_by_ticker["SPY"].index.date <= current_date]
                vol_percentile = compute_market_vol_percentile(spy_bars_so_far)
                multiplier = market_vol_size_multiplier(
                    vol_percentile, min_multiplier=market_vol_min_multiplier, max_multiplier=market_vol_max_multiplier,
                )
                position_size_pct = position_size_pct * multiplier

            if use_circuit_breaker:
                # Same walk-forward guarantee as Kelly above - only
                # trades closed before this point in the loop.
                cb_multiplier = circuit_breaker_multiplier(
                    [t.pnl for t in closed_trades],
                    loss_streak_threshold=circuit_breaker_loss_streak,
                    reduction_factor=circuit_breaker_reduction,
                )
                position_size_pct = position_size_pct * cb_multiplier

            if use_conviction_sizing:
                conv_multiplier = conviction_multiplier(
                    result.relative_volume,
                    min_multiplier=conviction_min_multiplier,
                    max_multiplier=conviction_max_multiplier,
                )
                position_size_pct = position_size_pct * conv_multiplier

            plan = size_position(
                spread,
                account_equity=equity,
                long_leg_price=long_price,
                short_leg_price=short_price,
                open_position_count=open_count,
                position_size_pct=position_size_pct,
            )
            if plan is None:
                continue

            if use_atr_adaptive_stops:
                atr_series = atr_pct_by_ticker.get(ticker)
                atr_at_entry = atr_series.loc[atr_series.index.date == current_date]
                atr_pct_now = float(atr_at_entry.iloc[0]) if len(atr_at_entry) and not pd.isna(atr_at_entry.iloc[0]) else baseline_atr_pct
                position_profit_target_pct, position_stop_loss_pct = adaptive_thresholds(
                    atr_pct_now, baseline_atr_pct, profit_target_pct, stop_loss_pct,
                    min_scale=atr_min_scale, max_scale=atr_max_scale,
                )
            else:
                position_profit_target_pct, position_stop_loss_pct = profit_target_pct, stop_loss_pct

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
                    peak_value=plan.est_cost_per_contract,
                    profit_target_pct=position_profit_target_pct,
                    stop_loss_pct=position_stop_loss_pct,
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
