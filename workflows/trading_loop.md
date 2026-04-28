# Trading Loop Workflow

## Objective
Run one complete trading cycle: assess state → manage existing positions → generate signals → apply risk rules → execute orders → log results.

## Universe
- **Equities (market hours only):** AAPL, MSFT, NVDA, AMZN, TSLA, SPY, QQQ, GOOGL
- **Crypto (24/7):** BTC/USD, ETH/USD

## Strategy
EMA(9,21) crossover + RSI(14) filter on 5-min bars (equities) / 15-min bars (crypto).
- BUY: fast EMA crosses above slow EMA AND RSI < 70
- SHORT: fast EMA crosses below slow EMA AND RSI > 30 (equity only)
- SELL/COVER: reverse crossover (equity) or bearish crossover (crypto — closes long)
- EOD: all equity positions closed 15 minutes before market close

## Step 1 — Portfolio Assessment
```
python tools/portfolio_status.py
```
Review: account_value, day_pnl_pct, open positions, market status.
**STOP** if `day_pnl_pct < -2.0%`. Do not resume without manual review.

## Step 2 — Run Full Cycle
```
python tools/trader_loop.py
```
This handles all steps automatically. Review the CycleResult JSON printed to stdout.

### Flags
| Flag | Effect |
|------|--------|
| `--dry-run` | Generate signals, print decisions, place NO orders |
| `--equity-only` | Skip crypto cycle |
| `--crypto-only` | Skip equity cycle (use when market is closed) |
| `--status` | Print portfolio summary and exit immediately |

## Step 3 — Review Output
CycleResult fields to check:
- `halted: true` → daily loss limit hit, **do not restart automatically**
- `errors: [...]` → inspect each error, fix if systematic
- `orders_placed` vs `orders_filled` → if consistently 0 fills, check spreads or market hours
- `mode: "idle"` → market closed and no crypto positions to manage (normal overnight)

## Error Handling

| Error | Cause | Action |
|-------|-------|--------|
| `AlpacaAPIError [429]` | Rate limited | Wait 60s, retry once |
| `AlpacaAPIError [403]` | Bad credentials | Check `.env`, verify paper keys at app.alpaca.markets |
| `AlpacaAPIError [422]` | Bad order params | Review risk_manager output, check qty/price |
| `AlpacaAPIError [500]` | Alpaca server error | Skip cycle, next run will retry |
| `No bars for SYMBOL` | Market closed or data issue | Normal pre/post market; auto-skipped |
| `insufficient bars` | Not enough history | Auto-skipped; check if symbol is newly listed |
| `Duplicate open order` | Cycle re-ran before fill | Normal; dedup prevents double-fill |
| `halted=True` | Daily loss > 2% | Review logs, fix root cause, restart manually |

## Edge Cases
- **Pre-market (4:00–9:30 ET):** Equity cycle auto-skips (`is_market_open=False`). Crypto runs.
- **Post-market (16:00–20:00 ET):** Same as pre-market.
- **Weekends:** Equity idle. Crypto runs normally.
- **Holidays:** `is_market_open=False` all day. Crypto runs.
- **EOD (≤15 min to close):** All equity positions auto-closed, all open orders cancelled.
- **Partial fills:** Logged; stop-limit placed on filled qty only (partial is handled in execute_orders).

## Logging
Logs written to `logs/trading.log` as structured JSON, rotated daily, 30-day retention.
To trace a full cycle: filter by `cycle_id` field.
```
findstr "abc12345" logs\trading.log
```
