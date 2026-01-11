from __future__ import annotations

import time
from typing import Dict, Optional

import pandas as pd

from .mt5_bridge import MT5Bridge
from .strategy_engine import PineParityEngine, Signal
from .telegram_notify import TelegramNotifier
from .timeframes import mt5_tf, tf_seconds
from .utils.time_utils import now_ms, ms_to_iso
from .utils.logger import setup_logger

log = setup_logger("mt5_feed")

def _rates_to_df(rates) -> pd.DataFrame:
    return pd.DataFrame(rates)

class MT5FeedRunner:
    def __init__(
        self,
        bridge: MT5Bridge,
        engine: PineParityEngine,
        timeframe: str,
        notifier: Optional[TelegramNotifier] = None,
        notify_stale: bool = False,
    ):
        self.bridge = bridge
        self.engine = engine
        self.timeframe = timeframe.upper()
        self.tf = mt5_tf(self.timeframe)
        self.tf_sec = tf_seconds(self.timeframe)
        self.last_bar_time: Dict[str, int] = {}
        self.notifier = notifier
        self.notify_stale = notify_stale

        # --- stale feed diagnostics ---
        self.last_bar_close_ms: Dict[str, int] = {}
        self.last_stale_warn_ms: Dict[str, int] = {}
        self.stale_threshold_ms = 30 * 60 * 1000
        self.stale_warn_every_ms = 5 * 60 * 1000

    def _bar_close_ms_from_open_sec(self, open_sec: int) -> int:
        return int((open_sec + self.tf_sec) * 1000)

    def _stale_check(self, symbols) -> None:
        now = now_ms()
        for sym in symbols:
            last_close = self.last_bar_close_ms.get(sym)
            if last_close is None:
                continue

            age_ms = now - last_close
            if age_ms <= self.stale_threshold_ms:
                continue

            last_warn = self.last_stale_warn_ms.get(sym, 0)
            if now - last_warn < self.stale_warn_every_ms:
                continue

            self.last_stale_warn_ms[sym] = now
            age_min = age_ms / 60000.0
            thr_min = self.stale_threshold_ms / 60000.0
            log.warning(
                f"STALE_FEED symbol={sym} tf={self.timeframe} "
                f"last_close={ms_to_iso(last_close)} age_min={age_min:.1f} "
                f"(no new bars for >{thr_min:.0f}m)"
            )
            if self.notify_stale and self.notifier and self.notifier.cfg.notify_stale_feed:
                msg = (
                    f"STALE_FEED symbol={sym} tf={self.timeframe} "
                    f"last_close={ms_to_iso(last_close)} age_min={age_min:.1f} "
                    f"(no new bars for >{thr_min:.0f}m)"
                )
                self.notifier.send(msg, key=f"stale:{sym}")

    def poll_symbol(self, symbol: str, tf_bars: int = 600, m1_bars: int = 3000) -> Optional[Signal]:
        rates_tf = self.bridge.copy_rates(symbol, self.tf, tf_bars)
        df_tf = _rates_to_df(rates_tf)
        if len(df_tf) < 3:
            return None

        # last row is forming; process last closed = -2
        last_closed = df_tf.iloc[-2]
        open_sec = int(last_closed["time"])

        prev = self.last_bar_time.get(symbol)
        if prev is not None and open_sec <= prev:
            return None
        self.last_bar_time[symbol] = open_sec
        log.info(
            f"BAR_CLOSE symbol={symbol} tf={self.timeframe} open_sec={open_sec} "
            f"close_ms={self._bar_close_ms_from_open_sec(open_sec)}"
        )

        df_tf_closed = df_tf.iloc[:-1].copy()

        # M1 up to close of last_closed
        rates_m1 = self.bridge.copy_rates(symbol, mt5_tf("M1"), m1_bars)
        df_1m = _rates_to_df(rates_m1)
        close_sec = open_sec + self.tf_sec
        df_1m = df_1m[df_1m["time"] < close_sec].copy()
        if len(df_1m) < 10:
            return None

        close_ms = self._bar_close_ms_from_open_sec(open_sec)
        self.last_bar_close_ms[symbol] = close_ms
        sig = self.engine.on_tf_bar_close(symbol=symbol, df_tf=df_tf_closed, df_1m=df_1m, bar_close_ms=close_ms)
        if sig:
            log.info(
                f"SIGNAL {sig.side} {sig.symbol} tf={sig.tf} entry={sig.entry_price:.5f} "
                f"cvd_ok={sig.cvd_ok} cvd={sig.cvd:.2f} thr={sig.cvd_thr:.2f} confirm_time_ms={sig.confirm_time_ms}"
            )
        return sig

    def run_forever(self, symbols, on_signal, poll_seconds: float = 1.0) -> None:
        log.info(f"MT5 master mode running. timeframe={self.timeframe} symbols={symbols}")
        while True:
            for sym in symbols:
                try:
                    sig = self.poll_symbol(sym)
                    if sig:
                        on_signal(sig)
                except Exception as e:
                    log.error(f"poll error symbol={sym}: {e}")
            self._stale_check(symbols)
            time.sleep(poll_seconds)
