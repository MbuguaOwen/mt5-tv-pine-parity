from __future__ import annotations

import json
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set

import pandas as pd

from .config import BinanceConfig
from .strategy_engine import PineParityEngine, Signal
from .timeframes import to_binance_interval
from .utils.logger import setup_logger

log = setup_logger("binance_feed")


@dataclass
class BinanceSignal:
    signal: Signal
    atr: Optional[float]
    close_time_ms: int


class BinanceFeedRunner:
    """Simple polling runner for Binance klines.

    Supports:
      - spot   -> https://api.binance.com/api/v3/klines
      - usdm   -> https://fapi.binance.com/fapi/v1/klines

    NOTE: Metals symbols like XAGUSDT / XAUUSDT are *Futures* products on Binance.
    If you run venue=spot, you'll get HTTP 400 (invalid symbol).
    """
    def __init__(self, cfg: BinanceConfig, engine: PineParityEngine, timeframe: str):
        self.cfg = cfg
        self.engine = engine
        self.interval = to_binance_interval(timeframe)
        self.last_close_ms: Dict[str, int] = {}
        self._valid_symbols: Optional[Set[str]] = None
        self._valid_symbols_ts: float = 0.0
        self._exchangeinfo_ttl_s: float = 60.0 * 60.0

    def _venue(self) -> str:
        return str(self.cfg.venue or "spot").lower().strip()

    def _api_base(self) -> str:
        if self.cfg.api_base:
            return self.cfg.api_base.rstrip("/")
        if self._venue() == "usdm":
            return "https://fapi.binance.com"
        return "https://api.binance.com"

    def _kline_path(self) -> str:
        if self._venue() == "usdm":
            return "/fapi/v1/klines"
        return "/api/v3/klines"

    def _exchange_info_path(self) -> str:
        if self._venue() == "usdm":
            return "/fapi/v1/exchangeInfo"
        return "/api/v3/exchangeInfo"

    def _fetch_json(self, url: str):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                raw = resp.read()
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            raise RuntimeError(f"HTTP {e.code} {e.reason} url={url} body={body[:300]}") from e

    def _fetch_klines(self, symbol: str, interval: str, limit: int):
        limit = min(int(limit), 1000)
        params = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": interval,
                "limit": int(limit),
            }
        )
        url = f"{self._api_base()}{self._kline_path()}?{params}"
        return self._fetch_json(url)

    def _get_valid_symbols(self, force: bool = False) -> Set[str]:
        now = time.time()
        if (not force) and self._valid_symbols is not None and (now - self._valid_symbols_ts) < self._exchangeinfo_ttl_s:
            return self._valid_symbols
        url = f"{self._api_base()}{self._exchange_info_path()}"
        data = self._fetch_json(url)
        syms: Set[str] = set()
        for s in data.get("symbols", []) if isinstance(data, dict) else []:
            sym = s.get("symbol")
            status = str(s.get("status", "")).upper()
            if sym and status in ("TRADING", "PRE_TRADING", "PENDING_TRADING"):
                syms.add(str(sym))
        self._valid_symbols = syms
        self._valid_symbols_ts = now
        return syms

    def _validate_symbols(self, symbols: Iterable[str]) -> List[str]:
        try:
            valid = self._get_valid_symbols()
        except Exception as e:
            log.warning(f"exchangeInfo fetch failed; skipping symbol validation err={e}")
            return list(symbols)
        ok: List[str] = []
        bad: List[str] = []
        for s in symbols:
            (ok if s in valid else bad).append(s)
        if bad:
            hint = ""
            if self._venue() == "spot":
                hint = " (Hint: metals like XAGUSDT/XAUUSDT are Futures; set binance.venue=usdm)"
            log.error(f"Invalid Binance symbols for venue={self._venue()}: {bad}{hint}")
        return ok

    @staticmethod
    def _klines_to_df(klines) -> pd.DataFrame:
        cols = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "num_trades",
            "taker_base_vol",
            "taker_quote_vol",
            "ignore",
        ]
        df = pd.DataFrame(klines, columns=cols)
        if df.empty:
            return df
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["open_time"] = df["open_time"].astype(int)
        df["close_time"] = df["close_time"].astype(int)
        df["tick_volume"] = df["volume"]
        return df

    def _m1_limit(self) -> int:
        cvd_len = int(getattr(self.engine.cfg, "cvdLenMin", 60))
        return min(1000, max(cvd_len + 10, 200))

    def poll_symbol(self, symbol: str) -> Optional[BinanceSignal]:
        if self._valid_symbols is not None and symbol not in self._valid_symbols:
            return None
        try:
            klines = self._fetch_klines(symbol, self.interval, self.cfg.limit)
        except Exception as e:
            log.error(f"fetch klines failed symbol={symbol} interval={self.interval} err={e}")
            return None
        if not isinstance(klines, list):
            log.error(f"fetch klines bad response symbol={symbol} interval={self.interval}")
            return None

        df_tf = self._klines_to_df(klines)
        if len(df_tf) < 3:
            return None

        last_closed = df_tf.iloc[-2]
        close_time_ms = int(last_closed["close_time"])
        prev = self.last_close_ms.get(symbol)
        if prev is not None and close_time_ms <= prev:
            return None
        self.last_close_ms[symbol] = close_time_ms

        log.info(
            f"BINANCE_BAR_CLOSE symbol={symbol} tf={self.interval} close_ms={close_time_ms}"
        )

        df_tf_closed = df_tf.iloc[:-1].copy()

        try:
            m1_klines = self._fetch_klines(symbol, "1m", self._m1_limit())
        except Exception as e:
            log.error(f"fetch m1 failed symbol={symbol} err={e}")
            return None
        if not isinstance(m1_klines, list):
            log.error(f"fetch m1 bad response symbol={symbol}")
            return None

        df_1m = self._klines_to_df(m1_klines)
        if df_1m.empty:
            return None
        df_1m = df_1m[df_1m["close_time"] <= close_time_ms].copy()
        if len(df_1m) < 10:
            return None

        bar_close_ms = close_time_ms + 1
        sig = self.engine.on_tf_bar_close(
            symbol=symbol, df_tf=df_tf_closed, df_1m=df_1m, bar_close_ms=bar_close_ms
        )
        if not sig:
            return None

        atr_val = None
        try:
            atr_series = PineParityEngine._atr(df_tf_closed, 14)
            if len(atr_series) > 0:
                atr_val = float(atr_series.iloc[-1])
        except Exception:
            atr_val = None

        return BinanceSignal(signal=sig, atr=atr_val, close_time_ms=close_time_ms)

    def run_forever(
        self,
        symbols,
        on_signal: Callable[[BinanceSignal], None],
        poll_seconds: Optional[float] = None,
    ) -> None:
        ps = float(poll_seconds) if poll_seconds is not None else float(self.cfg.poll_seconds)
        symbols = self._validate_symbols(symbols)
        log.info(f"Binance master running. venue={self._venue()} interval={self.interval} symbols={symbols}")
        if not symbols:
            while True:
                time.sleep(60.0)
        while True:
            for sym in symbols:
                try:
                    out = self.poll_symbol(sym)
                    if out:
                        on_signal(out)
                except Exception as e:
                    log.error(f"poll error symbol={sym}: {e}")
            time.sleep(ps)
