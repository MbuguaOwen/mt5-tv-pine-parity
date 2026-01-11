from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def fetch_klines(symbol: str, interval: str, limit: int = 10):
    params = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": int(limit)})
    url = f"https://api.binance.com/api/v3/klines?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> int:
    symbol = "BTCUSDT"
    interval = "15m"
    klines = fetch_klines(symbol, interval, limit=10)
    if not klines:
        print("no klines returned")
        return 1
    last = klines[-1]
    close_time_ms = int(last[6])
    dt = datetime.fromtimestamp(close_time_ms / 1000.0, tz=timezone.utc)
    print(f"{symbol} {interval} last_close_time_utc={dt.isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
