"""
Send end-of-day portfolio summary to Telegram.
Includes today's trades, open positions, and a signal scan explaining
what the strategy saw and why it did or didn't act.
"""
import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from alpaca_client import get_trading_client, AlpacaAPIError
from portfolio_status import get_portfolio_summary
from fetch_market_data import fetch_stock_bars, fetch_crypto_bars
from strategy_signals import generate_signals, SignalResult
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderStatus

logger = logging.getLogger("trading.notify_telegram")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EQUITY_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "SPY", "QQQ", "GOOGL"]
CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]


def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    resp.raise_for_status()


def get_todays_orders() -> list[dict]:
    client = get_trading_client()
    # Fetch from market open today (13:30 UTC = 9:30 AM ET)
    today = datetime.now(timezone.utc).replace(hour=13, minute=30, second=0, microsecond=0)
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=today, limit=100)
        raw = client.get_orders(req)
        orders = []
        for o in raw:
            if o.status == OrderStatus.filled:
                orders.append({
                    "symbol": str(o.symbol),
                    "side": o.side.value if hasattr(o.side, "value") else str(o.side),
                    "qty": float(o.filled_qty or 0),
                    "price": float(o.filled_avg_price or 0),
                    "order_type": o.order_type.value if hasattr(o.order_type, "value") else str(o.order_type),
                    "submitted_at": o.submitted_at,
                })
        # Sort chronologically
        orders.sort(key=lambda x: x["submitted_at"] or datetime.min.replace(tzinfo=timezone.utc))
        return orders
    except Exception as e:
        logger.warning("get_todays_orders failed: %s", e)
        return []


def get_eod_signals() -> dict[str, SignalResult]:
    signals = {}
    try:
        equity_bars = fetch_stock_bars(EQUITY_SYMBOLS, timeframe="5Min", lookback_bars=30)
        signals.update(generate_signals(equity_bars, asset_class="equity"))
    except Exception as e:
        logger.warning("equity signal scan failed: %s", e)
    try:
        crypto_bars = fetch_crypto_bars(CRYPTO_SYMBOLS, timeframe="15Min", lookback_bars=30)
        signals.update(generate_signals(crypto_bars, asset_class="crypto"))
    except Exception as e:
        logger.warning("crypto signal scan failed: %s", e)
    return signals


def _order_type_label(order_type: str) -> str:
    if "stop" in order_type:
        return "stop"
    if "limit" in order_type:
        return "limit"
    return "market"


def _build_decision_summary(summary, orders: list[dict], signals: dict[str, SignalResult]) -> str:
    total = len(signals)
    actionable = [v for v in signals.values() if v.signal in ("BUY", "SHORT")]
    sells = [v for v in signals.values() if v.signal == "SELL"]
    entry_orders = [o for o in orders if _order_type_label(o["order_type"]) == "market"]

    s1 = f"The bot scanned {total} symbol(s) at end of day using EMA(9/21) trend detection filtered by RSI(14)."

    if actionable:
        syms = ", ".join(s.symbol for s in actionable)
        s2 = f"{len(actionable)} symbol(s) had an active trend signal ({syms}); the rest were filtered out by RSI or showed no clear trend."
    elif sells:
        s2 = f"No new entries were signaled — {len(sells)} crypto position(s) triggered a SELL to close longs on a bearish trend."
    else:
        s2 = f"All {total} symbol(s) returned HOLD — either no clear EMA trend or RSI was in a neutral zone."

    if entry_orders:
        s3 = f"{len(entry_orders)} market order(s) were executed; GTC stop-losses at 1.5% were placed automatically for each."
    elif actionable and not entry_orders:
        s3 = "Despite active signals, no orders were placed — positions in those symbols were likely already open or risk limits rejected the sizing."
    else:
        s3 = "No orders were placed today."

    pnl_dir = "up" if summary.day_pnl >= 0 else "down"
    total_exposure = sum(p.pct_of_portfolio for p in summary.positions)
    if summary.positions:
        s4 = f"Closed the day {pnl_dir} {abs(summary.day_pnl_pct):.2f}% with {len(summary.positions)} open position(s) at {total_exposure:.0f}% portfolio exposure."
    else:
        s4 = f"Closed the day {pnl_dir} {abs(summary.day_pnl_pct):.2f}% with no open positions (fully flat)."

    return f"{s1} {s2} {s3} {s4}"


def build_message(summary, orders: list[dict], signals: dict[str, SignalResult]) -> str:
    today_str = datetime.now(timezone.utc).strftime("%A %b %d")
    pnl_sign = "+" if summary.day_pnl >= 0 else ""
    pnl_emoji = "📈" if summary.day_pnl >= 0 else "📉"

    lines = [
        f"*Alpaca Paper — {today_str}*",
        "",
        f"{pnl_emoji} *{pnl_sign}${summary.day_pnl:,.2f}* ({pnl_sign}{summary.day_pnl_pct:.2f}%)  |  Portfolio `${summary.account_value:,.2f}`",
        f"Cash: `${summary.cash:,.2f}`",
        "",
    ]

    # --- Trades ---
    if orders:
        entry_orders = [o for o in orders if _order_type_label(o["order_type"]) == "market"]
        stop_orders  = [o for o in orders if _order_type_label(o["order_type"]) == "stop"]

        lines.append(f"📋 *{len(entry_orders)} trade(s) executed today*")
        for o in entry_orders:
            side_emoji = "🟢" if o["side"] == "buy" else "🔴"
            lines.append(f"  {side_emoji} {o['side'].upper()} {o['qty']:.0f}x {o['symbol']} @ `${o['price']:.2f}`")
        if stop_orders:
            lines.append(f"  _(+ {len(stop_orders)} stop-loss order(s) placed)_")
    else:
        lines.append("📋 *Trades today:* None")

    lines.append("")

    # --- Positions ---
    if summary.positions:
        lines.append(f"📌 *Open Positions ({len(summary.positions)})*")
        for pos in summary.positions:
            sign = "+" if pos.unrealized_pnl >= 0 else ""
            e = "🟢" if pos.unrealized_pnl >= 0 else "🔴"
            # Show stop and target levels
            stop = round(pos.avg_entry_price * (1 - 0.015), 2) if pos.side == "long" else round(pos.avg_entry_price * (1 + 0.015), 2)
            target = round(pos.avg_entry_price * (1 + 0.03), 2) if pos.side == "long" else round(pos.avg_entry_price * (1 - 0.03), 2)
            lines.append(
                f"  {e} *{pos.symbol}* {pos.qty:.0f}x {pos.side} @ `${pos.avg_entry_price:.2f}` → `${pos.current_price:.2f}` "
                f"({sign}{pos.unrealized_pnl_pct:.2f}%)"
            )
            lines.append(f"     Stop `${stop:.2f}` | Target `${target:.2f}` | {pos.pct_of_portfolio:.1f}% of portfolio")
    else:
        lines.append("📌 *Positions:* Flat")

    lines.append("")

    # --- Signal scan ---
    if signals:
        lines.append("📡 *EOD Signal Scan*")

        actionable = {k: v for k, v in signals.items() if v.signal not in ("HOLD", "SELL")}
        sells      = {k: v for k, v in signals.items() if v.signal == "SELL"}
        holds      = {k: v for k, v in signals.items() if v.signal == "HOLD"}

        if actionable:
            for sym, sig in actionable.items():
                e = "🟢" if sig.signal == "BUY" else "🔴"
                lines.append(f"  {e} *{sym}* → *{sig.signal}*")
                lines.append(f"     {sig.reasoning}")
                lines.append(f"     EMA9 `{sig.ema_fast:.2f}` / EMA21 `{sig.ema_slow:.2f}` | RSI `{sig.rsi:.1f}`")

        if sells:
            for sym, sig in sells.items():
                lines.append(f"  🔵 *{sym}* → *SELL* (close long)")
                lines.append(f"     {sig.reasoning}")

        if holds:
            lines.append(f"  ⏸ *HOLD* — {', '.join(holds.keys())}")
            # Show why with a representative example
            sample = next(iter(holds.values()))
            # Extract crossover state and RSI range
            rsi_values = [f"{s.rsi:.0f}" for s in holds.values()]
            lines.append(f"     RSI neutral or trend unclear. RSI range: {min(float(r) for r in rsi_values):.0f}–{max(float(r) for r in rsi_values):.0f}")

        if not signals:
            lines.append("  _No signals — insufficient bar data_")
    else:
        lines.append("📡 *Signal scan:* No data available")

    lines.append("")
    lines.append("🧠 *Decision Summary*")
    lines.append(_build_decision_summary(summary, orders, signals))

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        sys.exit(1)

    summary = get_portfolio_summary()
    orders = get_todays_orders()
    signals = get_eod_signals()
    message = build_message(summary, orders, signals)
    send_message(message)
    print("Sent:")
    print(message)
