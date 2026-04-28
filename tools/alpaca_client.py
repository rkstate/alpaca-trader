import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import OrderStatus
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.crypto import CryptoHistoricalDataClient

logger = logging.getLogger("trading.alpaca_client")

API_KEY = os.environ["ALPACA_API_KEY"]
SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]


class AlpacaAPIError(Exception):
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code


_trading_client: TradingClient = None
_stock_data_client: StockHistoricalDataClient = None
_crypto_data_client: CryptoHistoricalDataClient = None


def get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
    return _trading_client


def get_stock_data_client() -> StockHistoricalDataClient:
    global _stock_data_client
    if _stock_data_client is None:
        _stock_data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
    return _stock_data_client


def get_crypto_data_client() -> CryptoHistoricalDataClient:
    global _crypto_data_client
    if _crypto_data_client is None:
        _crypto_data_client = CryptoHistoricalDataClient(API_KEY, SECRET_KEY)
    return _crypto_data_client


def get_account() -> dict:
    try:
        acct = get_trading_client().get_account()
        return {
            "id": str(acct.id),
            "status": str(acct.status),
            "portfolio_value": float(acct.portfolio_value),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "equity": float(acct.equity),
            "last_equity": float(acct.last_equity),
            "day_trade_count": int(acct.daytrade_count),
            "pattern_day_trader": bool(acct.pattern_day_trader),
        }
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("get_account failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


def get_clock() -> dict:
    try:
        clock = get_trading_client().get_clock()
        return {
            "timestamp": clock.timestamp.isoformat(),
            "is_open": bool(clock.is_open),
            "next_open": clock.next_open.isoformat(),
            "next_close": clock.next_close.isoformat(),
        }
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("get_clock failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


def is_market_open() -> bool:
    return get_clock()["is_open"]


def minutes_to_close() -> float:
    from datetime import datetime, timezone
    clock = get_clock()
    if not clock["is_open"]:
        return float("inf")
    next_close = datetime.fromisoformat(clock["next_close"])
    now = datetime.now(timezone.utc)
    return (next_close - now).total_seconds() / 60


def minutes_to_open() -> float:
    from datetime import datetime, timezone
    clock = get_clock()
    if clock["is_open"]:
        return 0.0
    next_open = datetime.fromisoformat(clock["next_open"])
    now = datetime.now(timezone.utc)
    return max(0.0, (next_open - now).total_seconds() / 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        acct = get_account()
        clock = get_clock()
        print(json.dumps({
            "account": acct,
            "clock": clock,
            "market_open": clock["is_open"],
            "minutes_to_close": minutes_to_close() if clock["is_open"] else None,
        }, indent=2))
    except AlpacaAPIError as e:
        print(f"ERROR [{e.status_code}]: {e}", file=sys.stderr)
        sys.exit(1)
