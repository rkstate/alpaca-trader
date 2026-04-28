"""
Risk management — position sizing, stop-loss calculation, exposure checks.
Never places orders; only approves or rejects trade decisions.
"""
import sys
import json
import math
import logging
import argparse
from dataclasses import dataclass, asdict

logger = logging.getLogger("trading.risk_manager")

# Conservative defaults (can be overridden via env vars)
import os
MAX_POSITION_PCT     = float(os.getenv("MAX_POSITION_PCT",     "0.10"))
MAX_PORTFOLIO_EXP    = float(os.getenv("MAX_PORTFOLIO_EXP",    "0.80"))
MAX_DAILY_LOSS_PCT   = float(os.getenv("MAX_DAILY_LOSS_PCT",   "0.02"))
MAX_OPEN_POSITIONS   = int(os.getenv("MAX_OPEN_POSITIONS",     "5"))
STOP_LOSS_PCT        = float(os.getenv("STOP_LOSS_PCT",        "0.015"))
TAKE_PROFIT_PCT      = float(os.getenv("TAKE_PROFIT_PCT",      "0.03"))
MIN_SIGNAL_STRENGTH  = float(os.getenv("MIN_SIGNAL_STRENGTH",  "0.001"))
MAX_SPREAD_PCT       = float(os.getenv("MAX_SPREAD_PCT",       "0.005"))


@dataclass
class RiskDecision:
    approved: bool
    qty: float
    stop_loss_price: float
    take_profit_price: float
    rejection_reason: str


def check_daily_loss_limit(account: dict) -> tuple[bool, float]:
    """Returns (within_limit, daily_pnl_pct). Halt if daily_pnl_pct < -MAX_DAILY_LOSS_PCT."""
    portfolio_value = float(account.get("portfolio_value", 0))
    last_equity = float(account.get("last_equity", portfolio_value))
    if last_equity == 0:
        return True, 0.0
    daily_pnl_pct = (portfolio_value - last_equity) / last_equity
    within_limit = daily_pnl_pct > -MAX_DAILY_LOSS_PCT
    if not within_limit:
        logger.warning("Daily loss limit hit: %.2f%% (limit: -%.2f%%)",
                       daily_pnl_pct * 100, MAX_DAILY_LOSS_PCT * 100)
    return within_limit, daily_pnl_pct


def get_portfolio_exposure(account: dict, positions: list[dict]) -> float:
    """Total long market value as fraction of portfolio."""
    portfolio_value = float(account.get("portfolio_value", 1))
    if portfolio_value == 0:
        return 0.0
    total_long = sum(
        abs(float(p.get("market_value", 0)))
        for p in positions
        if float(p.get("market_value", 0)) > 0
    )
    return total_long / portfolio_value


def check_existing_position_risk(
    position: dict,
    current_price: float,
    stop_loss_price: float,
    take_profit_price: float,
) -> str:
    """Returns 'STOP', 'TARGET', or 'HOLD'."""
    side = str(position.get("side", "long")).lower()
    if side == "long":
        if current_price <= stop_loss_price:
            return "STOP"
        if current_price >= take_profit_price:
            return "TARGET"
    else:  # short
        if current_price >= stop_loss_price:
            return "STOP"
        if current_price <= take_profit_price:
            return "TARGET"
    return "HOLD"


def size_position(
    symbol: str,
    signal_side: str,       # "buy" or "sell" (short)
    signal_strength: float,
    account: dict,
    current_positions: list[dict],
    current_price: float,
    quote: dict,
) -> RiskDecision:
    reject = lambda reason: RiskDecision(False, 0, 0.0, 0.0, reason)

    # 1. Daily loss limit
    within_limit, daily_pnl_pct = check_daily_loss_limit(account)
    if not within_limit:
        return reject(f"Daily loss limit exceeded ({daily_pnl_pct*100:.2f}%)")

    # 2. Signal strength
    if signal_strength < MIN_SIGNAL_STRENGTH:
        return reject(f"Signal too weak ({signal_strength:.4f} < {MIN_SIGNAL_STRENGTH})")

    # 3. Spread check
    bid = float(quote.get("bid", 0))
    ask = float(quote.get("ask", 0))
    mid = (bid + ask) / 2 if bid and ask else current_price
    spread_pct = (ask - bid) / mid if mid > 0 and ask > bid else 0.0
    if spread_pct > MAX_SPREAD_PCT:
        return reject(f"Spread too wide ({spread_pct*100:.3f}% > {MAX_SPREAD_PCT*100:.3f}%)")

    # 4. Max open positions
    open_count = len(current_positions)
    # Don't count an existing position in the same symbol (we may be adding to it)
    existing_symbols = {str(p.get("symbol", "")).upper() for p in current_positions}
    if symbol.upper() not in existing_symbols and open_count >= MAX_OPEN_POSITIONS:
        return reject(f"Max open positions reached ({open_count})")

    # 5. Portfolio exposure
    exposure = get_portfolio_exposure(account, current_positions)
    if exposure >= MAX_PORTFOLIO_EXP:
        return reject(f"Portfolio exposure too high ({exposure*100:.1f}% >= {MAX_PORTFOLIO_EXP*100:.1f}%)")

    # 6. Position sizing
    portfolio_value = float(account.get("portfolio_value", 0))
    if current_price <= 0:
        return reject("Invalid price (zero or negative)")
    max_notional = portfolio_value * MAX_POSITION_PCT
    qty = math.floor(max_notional / current_price)
    if qty < 1:
        return reject(f"Computed qty < 1 (price={current_price:.2f}, budget={max_notional:.2f})")

    # 7. Stop and target prices
    if signal_side == "buy":
        stop_loss_price = round(current_price * (1 - STOP_LOSS_PCT), 4)
        take_profit_price = round(current_price * (1 + TAKE_PROFIT_PCT), 4)
    else:  # short
        stop_loss_price = round(current_price * (1 + STOP_LOSS_PCT), 4)
        take_profit_price = round(current_price * (1 - TAKE_PROFIT_PCT), 4)

    logger.info("%s: approved qty=%d stop=%.4f target=%.4f", symbol, qty, stop_loss_price, take_profit_price)
    return RiskDecision(True, qty, stop_loss_price, take_profit_price, "")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--side", default="buy", choices=["buy", "sell"])
    parser.add_argument("--price", type=float, default=185.0)
    parser.add_argument("--strength", type=float, default=0.005)
    args = parser.parse_args()

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from alpaca_client import get_account
    from alpaca.trading.client import TradingClient
    from alpaca_client import get_trading_client

    account = get_account()
    positions = [
        {"symbol": str(p.symbol), "side": str(p.side), "market_value": float(p.market_value)}
        for p in get_trading_client().get_all_positions()
    ]
    quote = {"bid": args.price * 0.999, "ask": args.price * 1.001}

    decision = size_position(args.symbol, args.side, args.strength, account, positions, args.price, quote)
    print(json.dumps(asdict(decision), indent=2))
