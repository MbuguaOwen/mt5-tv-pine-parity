# MT5 TV Bot Execution + MT5-Feed Pine-Parity Bot (LONG ONLY)

Two modes, both execute **exactly at the Pine "BOT" triangle bar close**.

## Modes
### Mode A (TV is master)
TradingView Pine `alert()` -> Webhook `/tv` -> Bot validates JSON -> MT5 market order.

- Pine must be set to **Alert Mode = JSON**.
- Your TV alert must be:
  - Condition: **This strategy**
  - Trigger: **Any alert() function call**
  - Webhook URL: `http://<your-vm-ip>/tv` (reverse proxied to `127.0.0.1:9001/tv`)

### Mode B (MT5 is master)
MT5 feed -> Bot runs **Python port of your Pine logic** (Donchian + Pivot divergence + CVD proxy + BOS confirm) -> MT5 execution.

- Signals are generated on **timeframe bar close** only.

> Fail-fast logic is intentionally excluded (per request).

---

## Quickstart
### 1) Create venv + install deps
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/mac:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2) Configure
Edit `configs/default.yaml`:
- Set MT5 credentials
- Set `mode: tv_master` or `mode: mt5_master`
- Set `tv_bridge.enabled: true/false`
- Set `symbols` and optional `symbol_map`

### 3) Run
```bash
python -m mt5_tv_pine_parity_bot --config configs/default.yaml
```

---

## Notes on parity
- **Mode A** trusts Pine for the signal and executes immediately on receipt.
- **Mode B** replicates the Pine logic at bar close, including:
  - Confirmed pivots (`pivotLen` left/right)
  - Donchian extremes + `extBandPct`
  - Oscillator = EMA((close-open)*volume, oscLen)
  - CVD proxy window (1m) = sum(signed_volume over last `cvdLenMin` minutes)
  - Dynamic CVD threshold percentile (rolling) or fixed threshold
  - BOS confirm logic (trigger line + ATR buffer)

---

## Troubleshooting
- If MT5 init fails, ensure the MT5 terminal is installed, running, and logged in.
- If webhooks fail, confirm your reverse proxy and that the bot is listening on `tv_bridge.port`.
- For safety, start with `paper: true` first.
