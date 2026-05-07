"""
Send end-of-day portfolio summary to Telegram.
"""
import os
import sys
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from portfolio_status import get_portfolio_summary

logger = logging.getLogger("trading.notify_telegram")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    resp.raise_for_status()


def build_message(summary) -> str:
    pnl_sign = "+" if summary.day_pnl >= 0 else ""
    pnl_emoji = "📈" if summary.day_pnl >= 0 else "📉"

    lines = [
        "*Alpaca Paper Trading — EOD Summary*",
        "",
        f"{pnl_emoji} Day P&L: `{pnl_sign}${summary.day_pnl:,.2f}` ({pnl_sign}{summary.day_pnl_pct:.2f}%)",
        f"Portfolio: `${summary.account_value:,.2f}`",
        f"Cash: `${summary.cash:,.2f}`",
        "",
    ]

    if summary.positions:
        lines.append(f"*Open Positions ({len(summary.positions)}):*")
        for pos in summary.positions:
            sign = "+" if pos.unrealized_pnl >= 0 else ""
            lines.append(
                f"  {pos.symbol}: {pos.qty} @ ${pos.avg_entry_price:.2f} "
                f"→ {sign}${pos.unrealized_pnl:.2f} ({sign}{pos.unrealized_pnl_pct:.2f}%)"
            )
    else:
        lines.append("_No open positions_")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        sys.exit(1)

    summary = get_portfolio_summary()
    message = build_message(summary)
    send_message(message)
    print("Sent:")
    print(message)
