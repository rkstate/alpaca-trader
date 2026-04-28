import sys
import json
import logging
import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockSnapshotRequest,
)
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from alpaca_client import get_stock_data_client, get_crypto_data_client, AlpacaAPIError

logger = logging.getLogger("trading.fetch_market_data")

TIMEFRAME_MAP = {
    "1Min":  (TimeFrame(1,  TimeFrameUnit.Minute), timedelta(minutes=1)),
    "5Min":  (TimeFrame(5,  TimeFrameUnit.Minute), timedelta(minutes=5)),
    "15Min": (TimeFrame(15, TimeFrameUnit.Minute), timedelta(minutes=15)),
    "1Hour": (TimeFrame(1,  TimeFrameUnit.Hour),   timedelta(hours=1)),
    "1Day":  (TimeFrame(1,  TimeFrameUnit.Day),    timedelta(days=1)),
}


def _start_time(timeframe: str, lookback_bars: int) -> datetime:
    _, delta = TIMEFRAME_MAP[timeframe]
    buffer = int(lookback_bars * 2.5)
    return datetime.now(timezone.utc) - delta * buffer


def _bars_to_df(bar_data) -> pd.DataFrame:
    """Convert alpaca-py bar response (BarSet or list of Bar) to DataFrame."""
    if hasattr(bar_data, "df"):
        return bar_data.df
    if isinstance(bar_data, list) and bar_data:
        records = []
        for b in bar_data:
            records.append({
                "timestamp": b.timestamp,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
                "vwap": float(b.vwap) if getattr(b, "vwap", None) else None,
            })
        df = pd.DataFrame(records)
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        return df
    return pd.DataFrame()


def fetch_stock_bars(
    symbols: list[str],
    timeframe: str = "5Min",
    lookback_bars: int = 50,
) -> dict[str, pd.DataFrame]:
    tf, _ = TIMEFRAME_MAP[timeframe]
    start = _start_time(timeframe, lookback_bars)
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
            adjustment="raw",
            feed="iex",
        )
        bars = get_stock_data_client().get_stock_bars(req)
        result = {}
        for sym in symbols:
            try:
                df = _bars_to_df(bars[sym]).sort_index()
                if len(df) < lookback_bars:
                    logger.warning("%s: only %d bars (need %d)", sym, len(df), lookback_bars)
                    result[sym] = df
                else:
                    result[sym] = df.tail(lookback_bars)
            except (KeyError, Exception) as e:
                logger.warning("No bars for %s: %s", sym, e)
        return result
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("fetch_stock_bars failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


def fetch_crypto_bars(
    symbols: list[str],
    timeframe: str = "15Min",
    lookback_bars: int = 50,
) -> dict[str, pd.DataFrame]:
    tf, _ = TIMEFRAME_MAP[timeframe]
    start = _start_time(timeframe, lookback_bars)
    try:
        req = CryptoBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=tf,
            start=start,
        )
        bars = get_crypto_data_client().get_crypto_bars(req)
        result = {}
        for sym in symbols:
            try:
                df = _bars_to_df(bars[sym]).sort_index()
                if len(df) < lookback_bars:
                    logger.warning("%s: only %d bars (need %d)", sym, len(df), lookback_bars)
                    result[sym] = df
                else:
                    result[sym] = df.tail(lookback_bars)
            except (KeyError, Exception) as e:
                logger.warning("No crypto bars for %s: %s", sym, e)
        return result
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("fetch_crypto_bars failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


def fetch_latest_quotes(symbols: list[str]) -> dict[str, dict]:
    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed="iex")
        quotes = get_stock_data_client().get_stock_latest_quote(req)
        result = {}
        for sym, q in quotes.items():
            result[sym] = {
                "bid": float(q.bid_price) if q.bid_price else 0.0,
                "ask": float(q.ask_price) if q.ask_price else 0.0,
                "bid_size": float(q.bid_size) if q.bid_size else 0.0,
                "ask_size": float(q.ask_size) if q.ask_size else 0.0,
            }
        return result
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("fetch_latest_quotes failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


def fetch_crypto_latest_quotes(symbols: list[str]) -> dict[str, dict]:
    try:
        req = CryptoLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = get_crypto_data_client().get_crypto_latest_quote(req)
        result = {}
        for sym, q in quotes.items():
            result[sym] = {
                "bid": float(q.bid_price) if q.bid_price else 0.0,
                "ask": float(q.ask_price) if q.ask_price else 0.0,
                "bid_size": float(q.bid_size) if q.bid_size else 0.0,
                "ask_size": float(q.ask_size) if q.ask_size else 0.0,
            }
        return result
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("fetch_crypto_latest_quotes failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


def fetch_snapshots(symbols: list[str]) -> dict[str, dict]:
    try:
        req = StockSnapshotRequest(symbol_or_symbols=symbols, feed="iex")
        snaps = get_stock_data_client().get_stock_snapshot(req)
        result = {}
        for sym, s in snaps.items():
            latest_trade = s.latest_trade
            latest_quote = s.latest_quote
            result[sym] = {
                "price": float(latest_trade.price) if latest_trade else 0.0,
                "bid": float(latest_quote.bid_price) if latest_quote and latest_quote.bid_price else 0.0,
                "ask": float(latest_quote.ask_price) if latest_quote and latest_quote.ask_price else 0.0,
            }
        return result
    except Exception as e:
        status = getattr(e, "status_code", None)
        logger.error("fetch_snapshots failed: %s", e)
        raise AlpacaAPIError(str(e), status) from e


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT"])
    parser.add_argument("--bars", type=int, default=20)
    parser.add_argument("--timeframe", default="5Min")
    parser.add_argument("--crypto", action="store_true")
    args = parser.parse_args()

    if args.crypto:
        data = fetch_crypto_bars(args.symbols, args.timeframe, args.bars)
    else:
        data = fetch_stock_bars(args.symbols, args.timeframe, args.bars)

    for sym, df in data.items():
        print(f"\n=== {sym} ({len(df)} bars) ===")
        print(df[["open", "high", "low", "close", "volume"]].tail(5).to_string())
