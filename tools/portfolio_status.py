"""
Aggregate account + positions + clock into a single portfolio snapshot.
"""
import sys
import json
import logging
from dataclasses import dataclass, asdict, field

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from alpaca_client import get_account, get_clock, get_trading_client, AlpacaAPIError
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

logger = logging.getLogger("trading.portfolio_status")


@dataclass
class PositionDetail:
    symbol: str
    qty: float
    side: str
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    pct_of_portfolio: float


@dataclass
class PortfolioSummary:
    account_value: float
    cash: float
    buying_power: float
    day_pnl: float
    day_pnl_pct: float
    positions: list[PositionDetail] = field(default_factory=list)
    open_orders_count: int = 0
    is_market_open: bool = False
    minutes_to_close: float = 0.0
    minutes_to_open: float = 0.0


def get_portfolio_summary() -> PortfolioSummary:
    account = get_account()
    clock = get_clock()
    client = get_trading_client()

    portfolio_value = float(account["portfolio_value"])
    last_equity = float(account["last_equity"])
    day_pnl = portfolio_value - last_equity
    day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0.0

    # Positions
    positions = []
    try:
        raw_positions = client.get_all_positions()
        for p in raw_positions:
            mv = float(p.market_value or 0)
            entry = float(p.avg_entry_price or 0)
            current = float(p.current_price or 0)
            upnl = float(p.unrealized_pl or 0)
            upnl_pct = float(p.unrealized_plpc or 0) * 100
            pct_port = (abs(mv) / portfolio_value * 100) if portfolio_value else 0.0
            positions.append(PositionDetail(
                symbol=str(p.symbol),
                qty=float(p.qty or 0),
                side=str(p.side.value) if hasattr(p.side, "value") else str(p.side),
                avg_entry_price=round(entry, 4),
                current_price=round(current, 4),
                market_value=round(mv, 2),
                unrealized_pnl=round(upnl, 2),
                unrealized_pnl_pct=round(upnl_pct, 2),
                pct_of_portfolio=round(pct_port, 2),
            ))
    except Exception as e:
        logger.error("get_all_positions failed: %s", e)

    # Open orders count
    open_orders_count = 0
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        open_orders_count = len(client.get_orders(req))
    except Exception as e:
        logger.error("get_orders failed: %s", e)

    # Clock
    is_open = clock["is_open"]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    next_close = datetime.fromisoformat(clock["next_close"])
    next_open = datetime.fromisoformat(clock["next_open"])
    minutes_to_close = (next_close - now).total_seconds() / 60 if is_open else 0.0
    minutes_to_open = max(0.0, (next_open - now).total_seconds() / 60) if not is_open else 0.0

    return PortfolioSummary(
        account_value=round(portfolio_value, 2),
        cash=round(float(account["cash"]), 2),
        buying_power=round(float(account["buying_power"]), 2),
        day_pnl=round(day_pnl, 2),
        day_pnl_pct=round(day_pnl_pct, 4),
        positions=positions,
        open_orders_count=open_orders_count,
        is_market_open=is_open,
        minutes_to_close=round(minutes_to_close, 1),
        minutes_to_open=round(minutes_to_open, 1),
    )


def print_summary(summary: PortfolioSummary) -> None:
    print(f"\n{'='*55}")
    print(f"  Portfolio Value : ${summary.account_value:>12,.2f}")
    print(f"  Cash            : ${summary.cash:>12,.2f}")
    print(f"  Buying Power    : ${summary.buying_power:>12,.2f}")
    pnl_sign = "+" if summary.day_pnl >= 0 else ""
    print(f"  Day P&L         : {pnl_sign}${summary.day_pnl:,.2f} ({pnl_sign}{summary.day_pnl_pct:.4f}%)")
    market_status = "OPEN" if summary.is_market_open else "CLOSED"
    if summary.is_market_open:
        print(f"  Market          : {market_status} ({summary.minutes_to_close:.0f}m to close)")
    else:
        print(f"  Market          : {market_status} ({summary.minutes_to_open:.0f}m to open)")
    print(f"  Open Orders     : {summary.open_orders_count}")
    print(f"{'='*55}")
    if summary.positions:
        print(f"  {'SYMBOL':<10} {'QTY':>6} {'SIDE':<6} {'ENTRY':>8} {'CURR':>8} {'P&L%':>7} {'PORT%':>6}")
        print(f"  {'-'*55}")
        for p in summary.positions:
            sign = "+" if p.unrealized_pnl_pct >= 0 else ""
            print(f"  {p.symbol:<10} {p.qty:>6.2f} {p.side:<6} {p.avg_entry_price:>8.2f} "
                  f"{p.current_price:>8.2f} {sign}{p.unrealized_pnl_pct:>6.2f}% {p.pct_of_portfolio:>5.1f}%")
    else:
        print("  No open positions.")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    summary = get_portfolio_summary()
    print_summary(summary)
    print(json.dumps(asdict(summary), indent=2))
