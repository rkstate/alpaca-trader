# Risk Management Workflow

## Hard Limits (Never Override)

| Rule | Value | Enforced in |
|------|-------|-------------|
| Daily loss halt | > 2% portfolio drawdown → stop ALL trading | `risk_manager.check_daily_loss_limit()` |
| Max position size | 10% of portfolio per symbol | `risk_manager.size_position()` |
| Max portfolio exposure | 80% deployed at once | `risk_manager.get_portfolio_exposure()` |
| Max open positions | 5 simultaneous | `risk_manager.size_position()` |
| Stop loss | 1.5% per position (GTC stop-limit order) | `execute_orders.place_stop_limit_order()` |
| Take profit | 3% per position | `risk_manager.check_existing_position_risk()` |
| EOD equity flatten | 15 min before close | `trader_loop._run_equity_cycle()` |
| Crypto: no shorting | Long-only | `strategy_signals.generate_signals(asset_class="crypto")` |

## Soft Limits (Auto-skipped, logged)

| Rule | Value | Reason |
|------|-------|--------|
| Min signal strength | 0.1% EMA separation | Avoids noise-driven trades |
| Max bid-ask spread | 0.5% of mid price | Excessive slippage risk |
| Min bars for signal | 35 bars (SLOW_PERIOD + RSI_PERIOD + 5) | EMA unreliable on short series |

## Position Sizing Formula
```
max_notional = portfolio_value × 0.10
qty = floor(max_notional / current_price)
```
Example: $100,000 portfolio, AAPL at $185 → budget = $10,000 → qty = 54 shares.

## Stop-Loss Order Mechanics
After every entry, a GTC stop-limit order is placed immediately:
- **Long:** stop = entry × 0.985, limit = stop × 0.998
- **Short:** stop = entry × 1.015, limit = stop × 1.002

The limit offset prevents the stop from failing to fill in a fast market (0.2% slippage buffer).

## Daily Reset
- At 9:30 ET: `last_equity` resets to opening portfolio value (handled by Alpaca).
- At 15:45 ET: all equity positions closed, all open orders cancelled.
- Crypto positions carry overnight — no daily reset.

## Overriding Defaults
All risk parameters can be adjusted via environment variables in `.env`:
```
MAX_POSITION_PCT=0.10
MAX_PORTFOLIO_EXP=0.80
MAX_DAILY_LOSS_PCT=0.02
MAX_OPEN_POSITIONS=5
STOP_LOSS_PCT=0.015
TAKE_PROFIT_PCT=0.03
MIN_SIGNAL_STRENGTH=0.001
MAX_SPREAD_PCT=0.005
```
Changes take effect on the next cycle (no restart needed).

## Halt & Resume Procedure
When `halted=True` in CycleResult:
1. Read `logs/trading.log` — filter by the cycle_id that triggered the halt
2. Identify which positions caused the loss
3. Review whether the loss was due to strategy, data issue, or a bug
4. Adjust parameters in `.env` if needed
5. Manually resume: run `python tools/trader_loop.py --dry-run` to confirm signals look correct before going live

**Never auto-restart after a halt.** The halt exists to prevent compounding losses.

## Pattern Day Trader (PDT) Note
Paper account starts at ~$100,000 (above the $25,000 PDT threshold).
If paper account equity drops below $25,000, day-trading restrictions apply.
`risk_manager` logs a warning but does not block trades — monitor manually.
