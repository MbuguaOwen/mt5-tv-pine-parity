from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from .utils.logger import setup_logger

log = setup_logger("mt5")

@dataclass
class OrderResult:
    ok: bool
    retcode: int
    comment: str
    order: int = 0

class MT5Bridge:
    def __init__(self, login: int, password: str, server: str, path: str = ""):
        self.login = login
        self.password = password
        self.server = server
        self.path = path

    def connect(self) -> None:
        if self.path:
            ok = mt5.initialize(self.path, login=self.login, password=self.password, server=self.server)
        else:
            ok = mt5.initialize(login=self.login, password=self.password, server=self.server)

        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError("MT5 account_info() failed")
        log.info(f"Connected to MT5: login={acc.login} broker={acc.company} balance={acc.balance}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def ensure_symbol(self, symbol: str) -> None:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"MT5 symbol not found: {symbol}")
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(f"Failed to select symbol: {symbol}")

    def get_tick(self, symbol: str):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol_info_tick failed for {symbol}")
        return tick

    def place_market_buy(
        self,
        symbol: str,
        lot: float,
        deviation: int,
        magic: int,
        comment: str,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        paper: bool = True,
    ) -> OrderResult:
        self.ensure_symbol(symbol)

        tick = self.get_tick(symbol)
        price = float(tick.ask)

        if paper:
            log.info(f"[PAPER] BUY {symbol} lot={lot} price~{price} sl={sl} tp={tp}")
            return OrderResult(ok=True, retcode=0, comment="PAPER", order=0)

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": mt5.ORDER_TYPE_BUY,
            "price": price,
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        if sl is not None:
            req["sl"] = float(sl)
        if tp is not None:
            req["tp"] = float(tp)

        res = mt5.order_send(req)
        if res is None:
            err = mt5.last_error()
            return OrderResult(ok=False, retcode=-1, comment=f"order_send None, err={err}", order=0)

        ok = (res.retcode == mt5.TRADE_RETCODE_DONE)
        return OrderResult(ok=ok, retcode=int(res.retcode), comment=str(res.comment), order=int(getattr(res, "order", 0) or 0))

    def copy_rates(self, symbol: str, timeframe: int, count: int):
        self.ensure_symbol(symbol)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            raise RuntimeError(f"copy_rates_from_pos failed for {symbol} tf={timeframe}")
        return rates

    def order_calc_profit(self, order_type: int, symbol: str, lot: float, price_open: float, price_close: float) -> float:
        self.ensure_symbol(symbol)
        val = mt5.order_calc_profit(order_type, symbol, float(lot), float(price_open), float(price_close))
        if val is None:
            raise RuntimeError(f"order_calc_profit failed for {symbol}")
        return float(val)

    def has_open_position(self, symbol: str, magic: int) -> bool:
        pos = mt5.positions_get(symbol=symbol)
        if not pos:
            return False
        for p in pos:
            try:
                if int(getattr(p, "magic", 0)) == int(magic) and str(getattr(p, "symbol", "")) == symbol:
                    return True
            except Exception:
                continue
        return False
