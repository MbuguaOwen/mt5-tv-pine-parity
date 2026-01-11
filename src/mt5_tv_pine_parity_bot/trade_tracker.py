from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

from .telegram_notify import TelegramNotifier
from .utils.logger import setup_logger

log = setup_logger("trade_tracker")


@dataclass
class TradeMeta:
    mode: str  # "tv_master" or "binance_master"
    source: str  # "TV" or "MT5"
    symbol: str
    tf: str
    side: str  # "LONG" only
    lot: float
    entry_price: float
    sl: Optional[float]
    tp: Optional[float]
    confirm_time_ms: Optional[int] = None

    magic: int = 0
    comment: str = ""

    # Calculated at entry (account currency)
    risk_ccy: Optional[float] = None

    # MT5 ids (best-effort)
    position_ticket: Optional[int] = None

    opened_ts: float = 0.0
    max_price: Optional[float] = None
    min_price: Optional[float] = None


class TradeTracker:
    def __init__(
        self,
        notifier: TelegramNotifier,
        *,
        enabled: bool,
        poll_seconds: float,
        history_days: int,
        magic: int,
    ):
        self.notifier = notifier
        self.enabled = enabled
        self.poll_seconds = float(poll_seconds)
        self.history_days = int(history_days)
        self.magic = int(magic)

        self._lock = threading.Lock()
        self._open: Dict[str, TradeMeta] = {}  # key: symbol
        self._stop = False
        self._t: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._t and self._t.is_alive():
            return
        self._stop = False
        self._t = threading.Thread(target=self._loop, name="TradeTracker", daemon=True)
        self._t.start()
        log.info("started")

    def stop(self) -> None:
        self._stop = True

    def register_open(self, meta: TradeMeta) -> None:
        if not self.enabled:
            return
        meta.opened_ts = time.time()
        meta.max_price = meta.entry_price
        meta.min_price = meta.entry_price
        with self._lock:
            self._open[meta.symbol] = meta

    def _loop(self) -> None:
        while not self._stop:
            try:
                self._poll()
            except Exception as e:
                log.error(f"poll error: {e}")
            time.sleep(self.poll_seconds)

    def _poll(self) -> None:
        with self._lock:
            symbols = list(self._open.keys())

        if not symbols:
            return

        positions = mt5.positions_get()
        pos_by_symbol = {}
        if positions:
            for p in positions:
                try:
                    if int(getattr(p, "magic", 0)) != self.magic:
                        continue
                    pos_by_symbol[str(p.symbol)] = p
                except Exception:
                    continue

        for sym in symbols:
            with self._lock:
                meta = self._open.get(sym)
            if not meta:
                continue

            p = pos_by_symbol.get(sym)

            if p is not None:
                try:
                    price_cur = float(getattr(p, "price_current", meta.entry_price))
                    meta.max_price = max(meta.max_price or price_cur, price_cur)
                    meta.min_price = min(meta.min_price or price_cur, price_cur)
                    if not meta.position_ticket:
                        meta.position_ticket = int(getattr(p, "ticket", 0)) or None
                except Exception:
                    pass
                continue

            profit_ccy, exit_px, reason = self._calc_exit_from_history(meta)
            if profit_ccy is None:
                continue

            risk = float(meta.risk_ccy or 0.0)
            r_mult = (profit_ccy / abs(risk)) if risk else 0.0
            dur_sec = int(time.time() - meta.opened_ts)

            self._notify_exit(meta, profit_ccy, r_mult, exit_px, reason, dur_sec)

            with self._lock:
                self._open.pop(sym, None)

    def _calc_exit_from_history(self, meta: TradeMeta):
        frm = datetime.now() - timedelta(days=self.history_days)
        to = datetime.now()
        deals = mt5.history_deals_get(frm, to)
        if not deals:
            return (None, None, None)

        out: List[Any] = []
        for d in deals:
            try:
                if str(getattr(d, "symbol", "")) != meta.symbol:
                    continue
                if int(getattr(d, "magic", 0)) != self.magic:
                    continue
                if meta.position_ticket:
                    if int(getattr(d, "position_id", 0)) != int(meta.position_ticket):
                        continue
                if int(getattr(d, "entry", -1)) != mt5.DEAL_ENTRY_OUT:
                    continue
                out.append(d)
            except Exception:
                continue

        if not out:
            return (None, None, None)

        profit = 0.0
        px_num = 0.0
        px_den = 0.0
        for d in out:
            vol = float(getattr(d, "volume", 0.0) or 0.0)
            price = float(getattr(d, "price", 0.0) or 0.0)
            p = float(getattr(d, "profit", 0.0) or 0.0)
            c = float(getattr(d, "commission", 0.0) or 0.0)
            s = float(getattr(d, "swap", 0.0) or 0.0)
            profit += (p + c + s)

            if vol > 0 and price > 0:
                px_num += price * vol
                px_den += vol

        exit_px = (px_num / px_den) if px_den else None
        reason = "CLOSED"
        if exit_px is not None and meta.tp and abs(exit_px - meta.tp) <= (abs(meta.tp) * 0.0005):
            reason = "TP"
        if exit_px is not None and meta.sl and abs(exit_px - meta.sl) <= (abs(meta.sl) * 0.0005):
            reason = "SL"
        return (profit, exit_px, reason)

    def _notify_exit(
        self,
        meta: TradeMeta,
        profit_ccy: float,
        r_mult: float,
        exit_px: Optional[float],
        reason: str,
        dur_sec: int,
    ) -> None:
        if not self.notifier.cfg.notify_exit:
            return
        txt = (
            f"EXIT ({reason})\n"
            f"Mode: {meta.mode} | Source: {meta.source}\n"
            f"{meta.symbol} {meta.side} tf={meta.tf}\n"
            f"Entry: {meta.entry_price:.5f}\n"
            f"Exit:  {(exit_px if exit_px is not None else 0.0):.5f}\n"
            f"PnL:   {profit_ccy:.2f}\n"
            f"R:     {r_mult:.2f}R\n"
            f"Dur:   {dur_sec // 60}m\n"
        )
        self.notifier.send(txt, key=f"exit:{meta.symbol}")
