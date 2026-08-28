"""
Order execution and position sizing.

Places the multi-leg debit spread order via Alpaca. In the Hermes-driven
setup, tool calls to Alpaca's MCP server (place_option_order) typically
happen through the agent itself; this module holds the position-sizing
math and order-payload construction so that logic is testable outside
of an agent session, and reusable if you call Alpaca directly instead.

TODO: connect to Alpaca's trading client / MCP tool call for
place_option_order once the multi-leg payload shape is confirmed.
"""

from dataclasses import dataclass

from config.watchlist import POSITION_SIZE_PCT, MAX_CONCURRENT_POSITIONS
from src.options_selector import DebitSpread


@dataclass
class OrderPlan:
    spread: DebitSpread
    contracts: int
    est_cost_per_contract: float
    est_total_cost: float
    reasoning: str


def size_position(
    spread: DebitSpread,
    account_equity: float,
    long_leg_price: float,
    short_leg_price: float,
    open_position_count: int,
) -> OrderPlan | None:
    """
    Determine contract quantity for a debit spread given account equity
    and current open position count.

    Returns None if the max concurrent position limit is already reached.
    """
    if open_position_count >= MAX_CONCURRENT_POSITIONS:
        return None

    net_debit_per_contract = (long_leg_price - short_leg_price) * 100  # per contract
    if net_debit_per_contract <= 0:
        return None

    risk_budget = account_equity * POSITION_SIZE_PCT
    contracts = int(risk_budget // net_debit_per_contract)

    if contracts < 1:
        return None

    total_cost = contracts * net_debit_per_contract

    reasoning = (
        f"Risking {POSITION_SIZE_PCT:.0%} of equity "
        f"(${risk_budget:,.2f}) at a net debit of "
        f"${net_debit_per_contract:,.2f}/contract -> {contracts} contract(s), "
        f"total cost ${total_cost:,.2f}. "
        f"{open_position_count}/{MAX_CONCURRENT_POSITIONS} positions "
        f"currently open."
    )

    return OrderPlan(
        spread=spread,
        contracts=contracts,
        est_cost_per_contract=net_debit_per_contract,
        est_total_cost=total_cost,
        reasoning=reasoning,
    )


def build_order_payload(plan: OrderPlan) -> dict:
    """
    Build the multi-leg order payload shape expected by Alpaca's options
    order tools. Field names should be checked against the live
    place_option_order tool schema once available in a Hermes session.
    """
    return {
        "order_class": "mleg",
        "type": "limit",
        "time_in_force": "day",
        "qty": plan.contracts,
        "legs": [
            {
                "symbol": plan.spread.long_leg.symbol,
                "side": plan.spread.long_leg.side,
                "ratio_qty": 1,
            },
            {
                "symbol": plan.spread.short_leg.symbol,
                "side": plan.spread.short_leg.side,
                "ratio_qty": 1,
            },
        ],
    }
