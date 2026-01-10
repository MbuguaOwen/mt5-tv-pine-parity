from __future__ import annotations

from dataclasses import dataclass
from typing import Deque, Dict, Optional

from collections import deque

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .utils.logger import setup_logger

log = setup_logger("strategy")

@dataclass
class Signal:
    symbol: str
    side: str               # "LONG"
    entry_price: float
    confirm_time_ms: int
    pivot_price: float
    trigger: float
    cvd_ok: bool
    cvd: float
    cvd_thr: float
    tf: str

@dataclass
class SymbolState:
    lastPL_price: Optional[float] = None
    lastPL_osc: Optional[float] = None
    lastPL_bar: Optional[int] = None

    lastEntryBar: Optional[int] = None

    longSetup: bool = False
    longTrig: Optional[float] = None
    longPL: Optional[float] = None
    longSetBar: Optional[int] = None

    cvd_hist: Deque[float] = None

class PineParityEngine:
    """Python port of your Pine logic (LONG ONLY), excluding fail-fast."""

    def __init__(self, tf: str, cfg: StrategyConfig):
        self.tf = tf
        self.cfg = cfg
        self.state: Dict[str, SymbolState] = {}

    def _st(self, symbol: str) -> SymbolState:
        if symbol not in self.state:
            self.state[symbol] = SymbolState(cvd_hist=deque(maxlen=int(self.cfg.cvdLookbackBars)))
        return self.state[symbol]

    @staticmethod
    def _ema(series: pd.Series, length: int) -> pd.Series:
        return series.ewm(span=length, adjust=False).mean()

    @staticmethod
    def _rma(series: pd.Series, length: int) -> pd.Series:
        alpha = 1.0 / float(length)
        return series.ewm(alpha=alpha, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return PineParityEngine._rma(tr, length)

    @staticmethod
    def _pivotlow_confirmed(low: np.ndarray, i: int, left: int, right: int) -> Optional[int]:
        if i < left + right:
            return None
        piv = i - right
        w0 = piv - left
        w1 = piv + right
        if w0 < 0:
            return None
        window = low[w0 : w1 + 1]
        if window.size != (left + right + 1):
            return None
        mn = window.min()
        if low[piv] != mn:
            return None
        if (window == mn).sum() != 1:
            return None
        return piv

    @staticmethod
    def _percentile_linear(vals: np.ndarray, pct: float) -> float:
        if vals.size == 0:
            return float("nan")
        try:
            return float(np.percentile(vals, pct, method="linear"))
        except TypeError:
            return float(np.percentile(vals, pct, interpolation="linear"))

    def compute_cvd_proxy_1m(self, df_1m: pd.DataFrame) -> float:
        L = int(self.cfg.cvdLenMin)
        if len(df_1m) < L:
            L = len(df_1m)
        if L <= 0:
            return 0.0
        tail = df_1m.iloc[-L:]
        sv = np.where(
            tail["close"].to_numpy() >= tail["open"].to_numpy(),
            tail["tick_volume"].to_numpy(),
            -tail["tick_volume"].to_numpy(),
        )
        return float(np.sum(sv))

    def on_tf_bar_close(self, symbol: str, df_tf: pd.DataFrame, df_1m: pd.DataFrame, bar_close_ms: int) -> Optional[Signal]:
        st = self._st(symbol)
        cfg = self.cfg
        entry_mode = str(cfg.entryMode).strip().lower()

        min_need = max(cfg.donLen, 2 * cfg.pivotLen + 2, 50)
        if len(df_tf) < min_need:
            return None

        o = df_tf["open"].astype(float)
        h = df_tf["high"].astype(float)
        l = df_tf["low"].astype(float)
        c = df_tf["close"].astype(float)
        v = df_tf["tick_volume"].astype(float)

        donHi = h.rolling(cfg.donLen).max()
        donLo = l.rolling(cfg.donLen).min()
        rng = donHi - donLo
        loc = np.where(rng.to_numpy() > 0, (c - donLo) / rng, 0.5)

        osc_src = (c - o) * v
        osc = self._ema(osc_src, cfg.oscLen)
        atr = self._atr(df_tf, 14)
        hh_pivot = h.rolling(cfg.pivotLen).max()

        cvdProxy = self.compute_cvd_proxy_1m(df_1m)
        if cfg.useDynamicCvdPct:
            st.cvd_hist.append(cvdProxy)
            hist = np.array(st.cvd_hist, dtype=float)
            cvdThrUsed = self._percentile_linear(hist, float(cfg.cvdPct))
        else:
            cvdThrUsed = float(cfg.cvdThreshold)

        cvdGateLong = (not cfg.useCvdGate) or (cvdProxy >= cvdThrUsed)

        i = len(df_tf) - 1
        low_arr = l.to_numpy(dtype=float)

        def canEnter() -> bool:
            if int(cfg.cooldownBars) <= 0:
                return True
            if st.lastEntryBar is None:
                return True
            return (i - int(st.lastEntryBar)) >= int(cfg.cooldownBars)

        longSignal = False

        piv = self._pivotlow_confirmed(low_arr, i, cfg.pivotLen, cfg.pivotLen)
        if piv is not None:
            pl_price = float(low_arr[piv])
            pl_osc = float(osc.iloc[piv])
            loc_p = float(loc[piv])

            nearLower = loc_p <= float(cfg.extBandPct)
            hasPrev = st.lastPL_price is not None

            bullDiv = hasPrev and (pl_price <= float(st.lastPL_price)) and (pl_osc > float(st.lastPL_osc))
            safePrevOsc = max(abs(float(st.lastPL_osc)) if hasPrev else 0.0, 1e-9)
            oscChange = ((pl_osc - float(st.lastPL_osc)) / safePrevOsc) * 100.0 if hasPrev else 0.0
            strengthOk = (float(cfg.minDivStrength) <= 0.0) or (oscChange >= float(cfg.minDivStrength))

            if bool(cfg.longOnly) and nearLower and bullDiv and strengthOk:
                if canEnter():
                    st.longSetup = True
                    st.longTrig = float(hh_pivot.iloc[i])
                    st.longPL = pl_price
                    st.longSetBar = i

                    if entry_mode == "raw" and bool(cfg.tradeAllDivergences) and cvdGateLong:
                        longSignal = True
                        st.longSetup = False

            st.lastPL_price = pl_price
            st.lastPL_osc = pl_osc
            st.lastPL_bar = piv

        if entry_mode == "confirm" and st.longSetup and st.longSetBar is not None:
            if (i - int(st.longSetBar)) > int(cfg.maxWaitBars):
                st.longSetup = False
            else:
                buf = float(atr.iloc[i]) * float(cfg.bosAtrBuffer)
                trig = float(st.longTrig) if st.longTrig is not None else float("nan")
                bosOk = float(c.iloc[i]) > (trig + buf)
                trigOk = bosOk if bool(cfg.useBOSConfirm) else (float(c.iloc[i]) > float(o.iloc[i]))

                if trigOk and cvdGateLong and canEnter() and bool(cfg.tradeAllDivergences):
                    longSignal = True
                    st.longSetup = False

        if longSignal:
            st.lastEntryBar = i
            pivot_price = float(st.longPL) if st.longPL is not None else float("nan")
            trigger = float(st.longTrig) if st.longTrig is not None else float("nan")
            return Signal(
                symbol=symbol,
                side="LONG",
                entry_price=float(c.iloc[i]),
                confirm_time_ms=int(bar_close_ms - 1),
                pivot_price=pivot_price,
                trigger=trigger,
                cvd_ok=bool(cvdGateLong),
                cvd=float(cvdProxy),
                cvd_thr=float(cvdThrUsed),
                tf=self.tf,
            )

        return None
