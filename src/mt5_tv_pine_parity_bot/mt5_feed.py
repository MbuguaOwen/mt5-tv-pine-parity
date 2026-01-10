from __future__ import annotations

import time
from typing import Dict, Optional

import pandas as pd

from .mt5_bridge import MT5Bridge
from .strategy_engine import PineParityEngine, Signal
from .timeframes import mt5_tf, tf_seconds
from .utils.logger import setup_logger

log = setup_logger("mt5_feed")

def _rates_to_df(rates) -> pd.DataFrame:
    return pd.DataFrame(rates)

class MT5FeedRunner:
    def __init__(self, bridge: MT5Bridge, engine: PineParityEngine, timeframe: str):
        self.bridge = bridge
        self.engine = engine
        self.timeframe = timeframe.upper()
        self.tf = mt5_tf(self.timeframe)
        self.tf_sec = tf_seconds(self.timeframe)
        self.last_bar_time: Dict[str, int] = {}

    def _bar_close_ms_from_open_sec(self, open_sec: int) -> int:
        return int((open_sec + self.tf_sec) * 1000)

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
            time.sleep(poll_seconds)
