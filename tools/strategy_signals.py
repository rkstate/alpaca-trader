"""
Pure signal generation — no API calls, no side effects.
EMA(9,21) crossover + RSI(14) filter.
"""
import sys
import json
import logging
import argparse
from dataclasses import dataclass, asdict

import pandas as pd
import numpy as np

logger = logging.getLogger("trading.strategy_signals")

FAST_PERIOD = 9
SLOW_PERIOD = 21
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
MIN_BARS = SLOW_PERIOD + RSI_PERIOD - 5


@dataclass
class SignalResult:
    symbol: str
    signal: str          # BUY | SELL | SHORT | COVER | HOLD
    strength: float      # EMA separation as fraction of price
    ema_fast: float
    ema_slow: float
    rsi: float
    current_price: float
    reasoning: str


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def detect_trend(ema_fast: pd.Series, ema_slow: pd.Series) -> str:
    """Current trend state based on EMA relationship."""
    if len(ema_fast) < 1 or len(ema_slow) < 1:
        return "neutral"
    diff = ema_fast.iloc[-1] - ema_slow.iloc[-1]
    if diff > 0:
        return "bullish"
    if diff < 0:
        return "bearish"
    return "neutral"


def generate_signals(
    bars: dict[str, pd.DataFrame],
    fast_period: int = FAST_PERIOD,
    slow_period: int = SLOW_PERIOD,
    rsi_period: int = RSI_PERIOD,
    rsi_overbought: float = RSI_OVERBOUGHT,
    rsi_oversold: float = RSI_OVERSOLD,
    asset_class: str = "equity",  # "equity" or "crypto"
) -> dict[str, SignalResult]:
    results = {}
    for symbol, df in bars.items():
        if df is None or len(df) < MIN_BARS:
            logger.warning("%s: insufficient bars (%d < %d), skipping", symbol, len(df) if df is not None else 0, MIN_BARS)
            continue

        close = df["close"].astype(float)
        ema_fast = compute_ema(close, fast_period)
        ema_slow = compute_ema(close, slow_period)
        rsi = compute_rsi(close, rsi_period)

        trend = detect_trend(ema_fast, ema_slow)
        current_price = float(close.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_ema_fast = float(ema_fast.iloc[-1])
        current_ema_slow = float(ema_slow.iloc[-1])
        strength = abs(current_ema_fast - current_ema_slow) / current_price if current_price > 0 else 0.0

        if asset_class == "crypto":
            # Long-only for crypto
            if trend == "bullish" and current_rsi < rsi_overbought:
                signal = "BUY"
                reasoning = f"Bullish trend: EMA9 > EMA21 (RSI={current_rsi:.1f} < {rsi_overbought})"
            elif trend == "bearish":
                signal = "SELL"
                reasoning = f"Bearish trend: EMA9 < EMA21 — close long (RSI={current_rsi:.1f})"
            else:
                signal = "HOLD"
                reasoning = f"RSI filter active (RSI={current_rsi:.1f}, trend={trend})"
        else:
            # Equity: can go long or short
            if trend == "bullish" and current_rsi < rsi_overbought:
                signal = "BUY"
                reasoning = f"Bullish trend: EMA9 > EMA21 (RSI={current_rsi:.1f} < {rsi_overbought})"
            elif trend == "bearish" and current_rsi > rsi_oversold:
                signal = "SHORT"
                reasoning = f"Bearish trend: EMA9 < EMA21 (RSI={current_rsi:.1f} > {rsi_oversold})"
            else:
                signal = "HOLD"
                reasoning = f"RSI filter active (RSI={current_rsi:.1f}, trend={trend})"

        results[symbol] = SignalResult(
            symbol=symbol,
            signal=signal,
            strength=round(strength, 6),
            ema_fast=round(current_ema_fast, 4),
            ema_slow=round(current_ema_slow, 4),
            rsi=round(current_rsi, 2),
            current_price=round(current_price, 4),
            reasoning=reasoning,
        )
    return results


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from fetch_market_data import fetch_stock_bars, fetch_crypto_bars

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["AAPL", "MSFT", "NVDA"])
    parser.add_argument("--timeframe", default="5Min")
    parser.add_argument("--crypto", action="store_true")
    args = parser.parse_args()

    if args.crypto:
        bars = fetch_crypto_bars(args.symbols, args.timeframe, lookback_bars=60)
        signals = generate_signals(bars, asset_class="crypto")
    else:
        bars = fetch_stock_bars(args.symbols, args.timeframe, lookback_bars=60)
        signals = generate_signals(bars, asset_class="equity")

    print(json.dumps({s: asdict(r) for s, r in signals.items()}, indent=2))
