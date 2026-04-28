"""
Order execution — place, cancel, monitor orders, close positions.
All trading actions go through this module.
"""
import sys
import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from alpaca_client import get_trading_client, AlpacaAPIError

from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopLimitOrderRequest,
    GetOrdersRequest,
    ClosePositionRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus

logger = logging.getLogger("trading.execute_orders")

CRYPTO_SYMBOLS = {"BTC/USD", "ETH/USD", "BTC/USDT", "ETH/USDT"}


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    filled_qty: float
    filled_avg_price: float
    submitted_at: str
    error: str


def _order_to_result(order, error: str = "") -> OrderResult:
    return OrderResult(
        order_id=str(order.id),
        symbol=str(order.symbol),
        side=str(order.side.value) if hasattr(order.side, "value") else str(order.side),
        qty=float(order.qty or 0),
        status=str(order.status.value) if hasattr(order.status, "value") else str(order.status),
        filled_qty=float(order.filled_qty or 0),
        filled_avg_price=float(order.filled_avg_price or 0),
        submitted_at=order.submitted_at.isoformat() if order.submitted_at else "",
        error=error,
    )


def _error_result(symbol: str, side: str, qty: float, error: str) -> OrderResult:
    return OrderResult(
        order_id="",
        symbol=symbol,
        side=side,
        qty=qty,
        status="error",
        filled_qty=0.0,
        filled_avg_price=0.0,
        submitted_at="",
        error=error,
    )


def _is_crypto(symbol: str) -> bool:
    return "/" in symbol or symbol.upper() in CRYPTO_SYMBOLS


def _has_open_order(symbol: str, side: str) -> bool:
    """Check for existing open order on the same symbol+side (deduplication)."""
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
        orders = get_trading_client().get_orders(req)
        side_val = side.lower()
        for o in orders:
            o_side = str(o.side.value).lower() if hasattr(o.side, "value") else str(o.side).lower()
            if o_side == side_val:
                logger.warning("Duplicate order detected: %s %s already open (%s)", side, symbol, o.id)
                return True
        return False
    except Exception as e:
        logger.error("_has_open_order check failed: %s", e)
        return False


def place_market_order(
    symbol: str,
    qty: float,
    side: str,
    time_in_force: str = "day",
) -> OrderResult:
    if _has_open_order(symbol, side):
        return _error_result(symbol, side, qty, "Duplicate open order detected, skipping")

    tif = TimeInForce.GTC if _is_crypto(symbol) else TimeInForce(time_in_force)
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

    try:
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
        )
        order = get_trading_client().submit_order(req)
        logger.info("Market order submitted: %s %s x%s [%s]", side, symbol, qty, order.id)
        return _order_to_result(order)
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("place_market_order %s %s failed [%s]: %s", side, symbol, status, e)
        return _error_result(symbol, side, qty, f"[{status}] {e}")


def place_stop_limit_order(
    symbol: str,
    qty: float,
    side: str,
    stop_price: float,
    limit_price: float,
    time_in_force: str = "gtc",
) -> OrderResult:
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    tif = TimeInForce(time_in_force)
    try:
        req = StopLimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            stop_price=stop_price,
            limit_price=limit_price,
            time_in_force=tif,
        )
        order = get_trading_client().submit_order(req)
        logger.info("Stop-limit submitted: %s %s x%s stop=%.4f limit=%.4f [%s]",
                    side, symbol, qty, stop_price, limit_price, order.id)
        return _order_to_result(order)
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("place_stop_limit %s %s failed [%s]: %s", side, symbol, status, e)
        return _error_result(symbol, side, qty, f"[{status}] {e}")


def cancel_order(order_id: str) -> bool:
    try:
        get_trading_client().cancel_order_by_id(order_id)
        logger.info("Cancelled order %s", order_id)
        return True
    except Exception as e:
        logger.error("cancel_order %s failed: %s", order_id, e)
        return False


def cancel_all_open_orders(symbol: Optional[str] = None) -> int:
    try:
        if symbol:
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            orders = get_trading_client().get_orders(req)
            count = 0
            for o in orders:
                if cancel_order(str(o.id)):
                    count += 1
            return count
        else:
            cancelled = get_trading_client().cancel_orders()
            count = len(cancelled) if cancelled else 0
            logger.info("Cancelled %d open orders", count)
            return count
    except Exception as e:
        logger.error("cancel_all_open_orders failed: %s", e)
        return 0


def get_order_status(order_id: str) -> OrderResult:
    try:
        order = get_trading_client().get_order_by_id(order_id)
        return _order_to_result(order)
    except Exception as e:
        logger.error("get_order_status %s failed: %s", order_id, e)
        return _error_result("", "", 0, str(e))


def close_position(symbol: str) -> OrderResult:
    try:
        order = get_trading_client().close_position(symbol)
        logger.info("Closed position: %s [%s]", symbol, order.id)
        return _order_to_result(order)
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("close_position %s failed [%s]: %s", symbol, status, e)
        return _error_result(symbol, "close", 0, f"[{status}] {e}")


def close_all_positions() -> list[OrderResult]:
    """Close all equity positions (used for EOD flatten — skips crypto)."""
    results = []
    try:
        positions = get_trading_client().get_all_positions()
        for p in positions:
            sym = str(p.symbol)
            if _is_crypto(sym):
                continue  # Never force-close crypto at EOD
            result = close_position(sym)
            results.append(result)
    except Exception as e:
        logger.error("close_all_positions failed: %s", e)
    return results


def monitor_open_orders(order_ids: list[str], timeout_seconds: int = 60) -> list[OrderResult]:
    """Poll until orders fill or timeout. Cancels unfilled orders after timeout."""
    deadline = time.time() + timeout_seconds
    pending = set(order_ids)
    results = {}

    while pending and time.time() < deadline:
        time.sleep(3)
        filled = set()
        for oid in list(pending):
            r = get_order_status(oid)
            results[oid] = r
            if r.status in ("filled", "cancelled", "expired", "error"):
                filled.add(oid)
        pending -= filled

    # Cancel anything still open after timeout
    for oid in pending:
        logger.warning("Order %s still open after %ds, cancelling", oid, timeout_seconds)
        cancel_order(oid)
        r = get_order_status(oid)
        results[oid] = r

    return list(results.values())


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["status", "cancel-all", "close-all"], default="status")
    args = parser.parse_args()

    if args.action == "status":
        positions = get_trading_client().get_all_positions()
        orders_req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        open_orders = get_trading_client().get_orders(orders_req)
        print(json.dumps({
            "open_positions": len(positions),
            "open_orders": len(open_orders),
        }, indent=2))
    elif args.action == "cancel-all":
        n = cancel_all_open_orders()
        print(f"Cancelled {n} orders")
    elif args.action == "close-all":
        results = close_all_positions()
        print(json.dumps([asdict(r) for r in results], indent=2))
