"""
Master trading orchestrator — runs one complete cycle.
Called repeatedly by Claude routines.

Usage:
  python tools/trader_loop.py                   # Full cycle
  python tools/trader_loop.py --dry-run         # Signals only, no orders
  python tools/trader_loop.py --equity-only
  python tools/trader_loop.py --crypto-only
  python tools/trader_loop.py --status          # Portfolio snapshot only
"""
import sys
import os
import json
import uuid
import logging
import logging.handlers
import argparse
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

# --- Logging setup -----------------------------------------------------------
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "trading.log"

_root = logging.getLogger("trading")
_root.setLevel(logging.INFO)
if not _root.handlers:
    _fh = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=30, encoding="utf-8"
    )
    _fh.setFormatter(logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}'
    ))
    _root.addHandler(_fh)
    _sh = logging.StreamHandler(sys.stderr)
    _sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _root.addHandler(_sh)

logger = logging.getLogger("trading.trader_loop")

# --- Imports -----------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
from alpaca_client import get_account, is_market_open, minutes_to_close, AlpacaAPIError
from fetch_market_data import (
    fetch_stock_bars, fetch_crypto_bars,
    fetch_latest_quotes, fetch_crypto_latest_quotes, fetch_snapshots,
)
from strategy_signals import generate_signals, SignalResult
from risk_manager import size_position, check_daily_loss_limit, check_existing_position_risk
from execute_orders import (
    place_market_order, place_stop_limit_order,
    cancel_all_open_orders, close_position, close_all_positions,
    monitor_open_orders,
)
from portfolio_status import get_portfolio_summary, print_summary, PortfolioSummary

# --- Universe ----------------------------------------------------------------
EQUITY_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "SPY", "QQQ", "GOOGL"]
CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]

EOD_FLATTEN_MINUTES = 15   # Close equity positions this many minutes before close


@dataclass
class CycleResult:
    cycle_id: str
    timestamp: str
    mode: str
    positions_managed: int = 0
    signals_generated: int = 0
    orders_placed: int = 0
    orders_filled: int = 0
    halted: bool = False
    errors: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _log_event(cycle_id: str, event: str, **kwargs):
    payload = json.dumps({"cycle_id": cycle_id, "event": event, **kwargs})
    logger.info(payload)


def _run_equity_cycle(
    cycle_id: str,
    summary: PortfolioSummary,
    dry_run: bool,
) -> tuple[int, int, int, list[str]]:
    """Returns (positions_managed, orders_placed, orders_filled, errors)."""
    positions_managed = 0
    orders_placed = 0
    orders_filled = 0
    errors = []

    account = asdict(summary)  # reuse summary data as account-like dict
    # Need the raw account dict for risk checks
    try:
        raw_account = get_account()
    except AlpacaAPIError as e:
        errors.append(f"get_account: {e}")
        return positions_managed, orders_placed, orders_filled, errors

    raw_positions = [
        {"symbol": p.symbol, "side": p.side, "market_value": p.market_value,
         "avg_entry_price": p.avg_entry_price, "current_price": p.current_price}
        for p in summary.positions
        if "/" not in p.symbol  # equity only
    ]

    # --- STEP 1: Manage existing equity positions ----------------------------
    if raw_positions:
        equity_syms = [p["symbol"] for p in raw_positions]
        try:
            snaps = fetch_snapshots(equity_syms)
        except Exception as e:
            snaps = {}
            errors.append(f"fetch_snapshots: {e}")

        for pos in raw_positions:
            sym = pos["symbol"]
            entry = float(pos["avg_entry_price"])
            if entry <= 0:
                continue
            current = float(snaps.get(sym, {}).get("price", pos["current_price"]))
            side = pos["side"]
            if side == "long":
                stop = entry * (1 - float(os.getenv("STOP_LOSS_PCT", "0.015")))
                target = entry * (1 + float(os.getenv("TAKE_PROFIT_PCT", "0.03")))
            else:
                stop = entry * (1 + float(os.getenv("STOP_LOSS_PCT", "0.015")))
                target = entry * (1 - float(os.getenv("TAKE_PROFIT_PCT", "0.03")))

            action = check_existing_position_risk(pos, current, stop, target)
            if action in ("STOP", "TARGET"):
                _log_event(cycle_id, "position_exit", symbol=sym, action=action,
                           current=current, stop=stop, target=target)
                if not dry_run:
                    result = close_position(sym)
                    if not result.error:
                        positions_managed += 1
                    else:
                        errors.append(f"close_position {sym}: {result.error}")
                else:
                    positions_managed += 1

    # --- STEP 2: Cancel stale open orders ------------------------------------
    if not dry_run:
        cancelled = cancel_all_open_orders()
        _log_event(cycle_id, "stale_orders_cancelled", count=cancelled)

    # --- STEP 3: Fetch bars + generate signals --------------------------------
    try:
        bars = fetch_stock_bars(EQUITY_SYMBOLS, timeframe="5Min", lookback_bars=60)
    except Exception as e:
        errors.append(f"fetch_stock_bars: {e}")
        return positions_managed, orders_placed, orders_filled, errors

    signals = generate_signals(bars, asset_class="equity")
    _log_event(cycle_id, "signals_generated", count=len(signals),
               signals={s: r.signal for s, r in signals.items()})

    # --- STEP 4: Risk check + execute -----------------------------------------
    try:
        quotes = fetch_latest_quotes(EQUITY_SYMBOLS)
    except Exception as e:
        quotes = {}
        errors.append(f"fetch_latest_quotes: {e}")

    placed_ids = []
    for sym, sig in signals.items():
        if sig.signal not in ("BUY", "SHORT"):
            continue

        order_side = "buy" if sig.signal == "BUY" else "sell"
        quote = quotes.get(sym, {"bid": sig.current_price * 0.999, "ask": sig.current_price * 1.001})

        decision = size_position(
            sym, order_side, sig.strength, raw_account,
            raw_positions, sig.current_price, quote,
        )
        _log_event(cycle_id, "risk_decision", symbol=sym, signal=sig.signal,
                   approved=decision.approved, qty=decision.qty,
                   reason=decision.rejection_reason)

        if not decision.approved:
            continue

        if dry_run:
            orders_placed += 1
            continue

        entry_result = place_market_order(sym, decision.qty, order_side)
        if entry_result.error:
            errors.append(f"place_market_order {sym}: {entry_result.error}")
            continue
        placed_ids.append(entry_result.order_id)
        orders_placed += 1
        _log_event(cycle_id, "order_placed", symbol=sym, side=order_side,
                   qty=decision.qty, order_id=entry_result.order_id)

        # Place GTC stop-loss immediately after entry
        stop_side = "sell" if order_side == "buy" else "buy"
        stop_limit = decision.stop_loss_price * (0.998 if stop_side == "sell" else 1.002)
        place_stop_limit_order(sym, decision.qty, stop_side,
                               decision.stop_loss_price, round(stop_limit, 4))

    # --- STEP 5: Confirm fills ------------------------------------------------
    if placed_ids and not dry_run:
        results = monitor_open_orders(placed_ids, timeout_seconds=30)
        orders_filled = sum(1 for r in results if r.status == "filled")

    # --- STEP 6: EOD flatten --------------------------------------------------
    if summary.is_market_open and summary.minutes_to_close <= EOD_FLATTEN_MINUTES:
        _log_event(cycle_id, "eod_flatten", minutes_to_close=summary.minutes_to_close)
        if not dry_run:
            close_results = close_all_positions()
            cancel_all_open_orders()
            _log_event(cycle_id, "eod_complete", closed=len(close_results))

    return positions_managed, orders_placed, orders_filled, errors


def _run_crypto_cycle(
    cycle_id: str,
    summary: PortfolioSummary,
    dry_run: bool,
) -> tuple[int, int, int, list[str]]:
    """Returns (positions_managed, orders_placed, orders_filled, errors)."""
    positions_managed = 0
    orders_placed = 0
    orders_filled = 0
    errors = []

    try:
        raw_account = get_account()
    except AlpacaAPIError as e:
        errors.append(f"get_account: {e}")
        return positions_managed, orders_placed, orders_filled, errors

    crypto_positions = [
        {"symbol": p.symbol, "side": p.side, "market_value": p.market_value,
         "avg_entry_price": p.avg_entry_price, "current_price": p.current_price}
        for p in summary.positions
        if "/" in p.symbol
    ]
    all_positions = [
        {"symbol": p.symbol, "side": p.side, "market_value": p.market_value}
        for p in summary.positions
    ]

    # Manage existing crypto positions
    if crypto_positions:
        try:
            crypto_quotes = fetch_crypto_latest_quotes([p["symbol"] for p in crypto_positions])
        except Exception as e:
            crypto_quotes = {}
            errors.append(f"fetch_crypto_latest_quotes: {e}")

        for pos in crypto_positions:
            sym = pos["symbol"]
            entry = float(pos["avg_entry_price"])
            current = float(crypto_quotes.get(sym, {}).get("ask", pos["current_price"]))
            if entry <= 0:
                continue
            stop = entry * (1 - float(os.getenv("STOP_LOSS_PCT", "0.015")))
            target = entry * (1 + float(os.getenv("TAKE_PROFIT_PCT", "0.03")))
            action = check_existing_position_risk(pos, current, stop, target)
            if action in ("STOP", "TARGET"):
                _log_event(cycle_id, "crypto_position_exit", symbol=sym, action=action)
                if not dry_run:
                    result = close_position(sym)
                    if not result.error:
                        positions_managed += 1
                    else:
                        errors.append(f"close_position {sym}: {result.error}")
                else:
                    positions_managed += 1

    # Fetch bars + signals
    try:
        bars = fetch_crypto_bars(CRYPTO_SYMBOLS, timeframe="15Min", lookback_bars=60)
    except Exception as e:
        errors.append(f"fetch_crypto_bars: {e}")
        return positions_managed, orders_placed, orders_filled, errors

    signals = generate_signals(bars, asset_class="crypto")
    _log_event(cycle_id, "crypto_signals", count=len(signals),
               signals={s: r.signal for s, r in signals.items()})

    try:
        quotes = fetch_crypto_latest_quotes(CRYPTO_SYMBOLS)
    except Exception as e:
        quotes = {}
        errors.append(f"fetch_crypto_latest_quotes: {e}")

    placed_ids = []
    for sym, sig in signals.items():
        if sig.signal == "BUY":
            quote = quotes.get(sym, {"bid": sig.current_price * 0.998, "ask": sig.current_price * 1.002})
            decision = size_position(sym, "buy", sig.strength, raw_account,
                                     all_positions, sig.current_price, quote)
            _log_event(cycle_id, "crypto_risk_decision", symbol=sym,
                       approved=decision.approved, reason=decision.rejection_reason)
            if not decision.approved:
                continue
            if dry_run:
                orders_placed += 1
                continue
            result = place_market_order(sym, decision.qty, "buy")
            if result.error:
                errors.append(f"crypto order {sym}: {result.error}")
            else:
                placed_ids.append(result.order_id)
                orders_placed += 1

        elif sig.signal == "SELL":
            # Close existing long crypto position
            has_long = any(p["symbol"] == sym and p["side"] == "long" for p in crypto_positions)
            if has_long and not dry_run:
                result = close_position(sym)
                if not result.error:
                    positions_managed += 1

    if placed_ids and not dry_run:
        results = monitor_open_orders(placed_ids, timeout_seconds=30)
        orders_filled = sum(1 for r in results if r.status == "filled")

    return positions_managed, orders_placed, orders_filled, errors


def run_cycle(
    equity_mode: bool = True,
    crypto_mode: bool = True,
    dry_run: bool = False,
) -> CycleResult:
    cycle_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now(timezone.utc).isoformat()

    result = CycleResult(cycle_id=cycle_id, timestamp=timestamp, mode="")
    _log_event(cycle_id, "cycle_start", equity=equity_mode, crypto=crypto_mode, dry_run=dry_run)

    # Portfolio snapshot
    try:
        summary = get_portfolio_summary()
    except Exception as e:
        result.errors.append(f"get_portfolio_summary: {e}")
        result.halted = True
        return result

    result.summary = {
        "account_value": summary.account_value,
        "day_pnl": summary.day_pnl,
        "day_pnl_pct": summary.day_pnl_pct,
        "open_positions": len(summary.positions),
        "is_market_open": summary.is_market_open,
    }

    # Daily loss halt check
    try:
        raw_account = get_account()
        within_limit, daily_pnl_pct = check_daily_loss_limit(raw_account)
        if not within_limit:
            result.halted = True
            result.errors.append(f"Daily loss limit exceeded ({daily_pnl_pct*100:.2f}%) — HALTED")
            _log_event(cycle_id, "halt", reason="daily_loss_limit", pnl_pct=daily_pnl_pct)
            return result
    except Exception as e:
        result.errors.append(f"daily_loss_check: {e}")

    modes = []
    pm = op = of = 0

    if equity_mode:
        if summary.is_market_open:
            modes.append("equity")
            _pm, _op, _of, errs = _run_equity_cycle(cycle_id, summary, dry_run)
            pm += _pm; op += _op; of += _of
            result.errors.extend(errs)
        else:
            _log_event(cycle_id, "equity_skipped", reason="market_closed")

    if crypto_mode:
        modes.append("crypto")
        _pm, _op, _of, errs = _run_crypto_cycle(cycle_id, summary, dry_run)
        pm += _pm; op += _op; of += _of
        result.errors.extend(errs)

    result.mode = "+".join(modes) if modes else "idle"
    result.positions_managed = pm
    result.signals_generated = len(EQUITY_SYMBOLS) + len(CRYPTO_SYMBOLS)
    result.orders_placed = op
    result.orders_filled = of

    _log_event(cycle_id, "cycle_end", mode=result.mode, orders_placed=op,
               orders_filled=of, positions_managed=pm, errors=len(result.errors))
    return result


def main():
    parser = argparse.ArgumentParser(description="Run one trading cycle")
    parser.add_argument("--dry-run", action="store_true", help="Signals only, no orders")
    parser.add_argument("--equity-only", action="store_true")
    parser.add_argument("--crypto-only", action="store_true")
    parser.add_argument("--status", action="store_true", help="Print portfolio status and exit")
    args = parser.parse_args()

    logging.getLogger("trading").setLevel(logging.INFO)

    if args.status:
        summary = get_portfolio_summary()
        print_summary(summary)
        return

    equity_mode = not args.crypto_only
    crypto_mode = not args.equity_only

    result = run_cycle(
        equity_mode=equity_mode,
        crypto_mode=crypto_mode,
        dry_run=args.dry_run,
    )

    print(json.dumps(asdict(result), indent=2))
    sys.exit(1 if result.halted or result.errors else 0)


if __name__ == "__main__":
    main()
