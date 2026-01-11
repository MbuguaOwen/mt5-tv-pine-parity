# MT5 TV Bot Execution + Binance-Feed Pine-Parity Bot (LONG ONLY)

Two modes, both execute **exactly at the Pine "BOT" triangle bar close**.

## Modes
### Mode A (tv_master)
TradingView Pine `alert()` -> Webhook `/tv` -> Bot validates JSON -> MT5 market order.

- Pine must be set to **Alert Mode = JSON**.
- Your TV alert must be:
  - Condition: **This strategy**
  - Trigger: **Any alert() function call**
  - Webhook URL: `http://<your-vm-ip>/tv` (reverse proxied to `127.0.0.1:9001/tv`)

### Mode B (binance_master)
Binance REST candles -> Bot runs **Python port of your Pine logic** -> MT5 execution.

- Signals are generated on **timeframe bar close** only.
- MT5 is **execution-only** in both modes.

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
Edit `configs/default.yaml`, or create a local override:
```bash
# Create a local file and ignore it locally:
copy configs\default.yaml configs\local.yaml
git update-index --assume-unchanged configs/local.yaml
# Or add it to .git/info/exclude manually:
echo configs/local.yaml >> .git/info/exclude
```

Key fields:
- `mode: tv_master` or `mode: binance_master`
- `timeframe: "15m"` (string)
- `expected_tf: "15m"` for TV mode
- `symbols`: Binance symbols (BTCUSDT, ETHUSDT, ...)
- `symbol_map`: Binance/TV symbol -> MT5 symbol

### 3) Run
```bash
python -m mt5_tv_pine_parity_bot --config configs/default.yaml
```

---

## Example local.yaml (TV master)
```yaml
mode: tv_master
timeframe: "15m"
expected_tf: "15m"
symbols: [BTCUSDT, ETHUSDT, SOLUSDT, PAXGUSDT, XAGUSDT]
symbol_map:
  BTCUSDT: BTCUSD.lv
  ETHUSDT: ETHUSD.lv
  SOLUSDT: SOLUSD.lv
  PAXGUSDT: XAUUSD
  XAGUSDT: XAGUSD

tv_bridge:
  enabled: true
  host: 127.0.0.1
  port: 9001
  path: /tv
  secret: "YOUR_TV_SECRET"
  require_tf_match: true

mt5:
  login: 0
  password: ""
  server: ""
  path: "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
```

## Example local.yaml (Binance master)
```yaml
mode: binance_master
timeframe: "15m"
symbols: [BTCUSDT, ETHUSDT, SOLUSDT, PAXGUSDT, XAGUSDT]
symbol_map:
  BTCUSDT: BTCUSD.lv
  ETHUSDT: ETHUSD.lv
  SOLUSDT: SOLUSD.lv
  PAXGUSDT: XAUUSD
  XAGUSDT: XAGUSD

binance:
  venue: spot   # spot | usdm
  poll_seconds: 1.0
  limit: 500

mt5:
  login: 0
  password: ""
  server: ""
  path: "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
```

---

## Binance sanity script
Fetch 10 klines and print the last close time:
```bash
python scripts/binance_klines_smoke.py
```

## Parity smoke test
Run with one symbol and watch for `BINANCE_BAR_CLOSE` logs:
```bash
python -m mt5_tv_pine_parity_bot --config configs/local.yaml
```

---

## Troubleshooting
- If MT5 init fails, ensure the MT5 terminal is installed, running, and logged in.
- If webhooks fail, confirm your reverse proxy and that the bot is listening on `tv_bridge.port`.
- For safety, start with `paper: true` first.
