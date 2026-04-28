# Setup Workflow

## Prerequisites
- Python 3.10+
- Alpaca paper trading account at app.alpaca.markets
- API keys stored in `.env` (already configured)

## Step 1 — Install Dependencies
```
cd "C:\Users\rohan\Documents\Claude Code\Trading"
pip install -r requirements.txt
```
Expected: alpaca-py, pandas, numpy, python-dotenv install cleanly.

## Step 2 — Verify Alpaca Connection
```
python tools/alpaca_client.py
```
Expected output: JSON with `portfolio_value ≈ 100000`, `status: "ACTIVE"`, `market_open: true/false`.
If you see `[403]`: credentials in `.env` are wrong. Re-check ALPACA_API_KEY and ALPACA_SECRET_KEY.

## Step 3 — Test Data Fetch
```
python tools/fetch_market_data.py --symbols AAPL MSFT --bars 20
```
Expected: 20 bars of OHLCV data per symbol, printed to stdout.
If bars are missing: market may be closed (pre-market). Try `--timeframe 1Day` instead.

## Step 4 — Test Signal Generation
```
python tools/strategy_signals.py --symbols AAPL MSFT NVDA
```
Expected: JSON with `signal: "BUY"/"SHORT"/"HOLD"` for each symbol.

## Step 5 — Dry Run (Full Cycle, No Orders)
```
python tools/trader_loop.py --dry-run
```
Expected: CycleResult JSON with `orders_placed > 0` if signals fired, `orders_filled: 0` (dry-run).
Check `errors: []` — should be empty on first run.

## Step 6 — Portfolio Status
```
python tools/trader_loop.py --status
```
Expected: Formatted table showing account value, positions, day P&L, market status.

## Step 7 — First Live Cycle
```
python tools/trader_loop.py --equity-only
```
Watch the output. Confirm orders appear in the Alpaca paper dashboard at app.alpaca.markets.
If orders are placed: verify stop-limit orders also appear (GTC, one per entry).

## Step 8 — Check Logs
```
type logs\trading.log
```
Each line is a JSON event. Look for `cycle_start`, `signals_generated`, `order_placed`, `cycle_end`.

## Claude Routines (Scheduling)
After verifying the system works, Claude routines are created to run cycles automatically:
- **Equity routine:** every 5 minutes, Mon–Fri during market hours
- **Crypto routine:** every 15 minutes, 24/7

Ask Claude: "Set up the trading routines to run trader_loop.py on schedule."

## Common Issues

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: alpaca` | Run `pip install alpaca-py` |
| `KeyError: ALPACA_API_KEY` | Check `.env` exists in the Trading/ root |
| `403 Forbidden` | Wrong API key; regenerate at app.alpaca.markets |
| `422 Unprocessable` | Order rejected; check qty > 0, price reasonable |
| `No bars returned` | Market closed; try `--crypto-only` or `--dry-run` after hours |
| `halted=True` immediately | Daily loss already > 2%; check if paper account was reset |
