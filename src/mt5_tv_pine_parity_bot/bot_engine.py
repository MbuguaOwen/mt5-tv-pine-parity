from __future__ import annotations

import asyncio
from typing import Dict, Optional

from .config import load_config, AppConfig
from .mt5_bridge import MT5Bridge
from .mt5_feed import MT5FeedRunner
from .strategy_engine import PineParityEngine, Signal
from .tv_bridge import TVSignal, start_server
from .timeframes import mt5_tf
from .utils.logger import setup_logger

log = setup_logger("engine")

def _map_symbol(cfg: AppConfig, symbol: str) -> str:
    return cfg.symbol_map.get(symbol, symbol)

def _calc_sl_tp_long(entry: float, atr: Optional[float], sl_mult: float, tp_mult: float):
    if atr is None or atr != atr or atr <= 0:
        return None, None
    sl = entry - atr * sl_mult
    tp = entry + atr * tp_mult
    return sl, tp

def run(config_path: str) -> int:
    cfg = load_config(config_path)

    bridge = MT5Bridge(cfg.mt5.login, cfg.mt5.password, cfg.mt5.server, cfg.mt5.path)
    bridge.connect()

    last_tv_confirm: Dict[str, int] = {}

    def _atr_hint(symbol: str) -> Optional[float]:
        try:
            import pandas as pd
            from .strategy_engine import PineParityEngine as PPE
            rates = bridge.copy_rates(symbol, mt5_tf(cfg.timeframe), 250)
            df = pd.DataFrame(rates)
            if len(df) > 50:
                atr_series = PPE._atr(df, 14)
                # last closed bar = -2 (same assumption as feed)
                return float(atr_series.iloc[-2])
        except Exception:
            return None
        return None

    def execute_long(symbol: str, entry_price: Optional[float]):
        if entry_price is None:
            log.error(f"Cannot execute: missing entry_price for {symbol}")
            return
        atr = _atr_hint(symbol)
        sl, tp = _calc_sl_tp_long(entry=float(entry_price), atr=atr, sl_mult=cfg.risk.sl_atr_mult, tp_mult=cfg.risk.tp_atr_mult)

        res = bridge.place_market_buy(
            symbol=symbol,
            lot=cfg.risk.lot,
            deviation=cfg.risk.deviation,
            magic=cfg.risk.magic,
            comment=cfg.risk.comment,
            sl=sl,
            tp=tp,
            paper=cfg.paper,
        )
        if res.ok:
            log.info(f"EXEC OK symbol={symbol} retcode={res.retcode} comment={res.comment} order={res.order}")
        else:
            log.error(f"EXEC FAIL symbol={symbol} retcode={res.retcode} comment={res.comment}")

    async def on_tv(sig: TVSignal):
        mt5_symbol = _map_symbol(cfg, sig.symbol)
        if sig.confirm_time_ms is not None:
            prev = last_tv_confirm.get(mt5_symbol)
            if prev is not None and sig.confirm_time_ms == prev:
                log.info(f"TV dedupe ignored symbol={mt5_symbol} confirm_time_ms={sig.confirm_time_ms}")
                return
            last_tv_confirm[mt5_symbol] = sig.confirm_time_ms

        log.info(f"TV SIGNAL LONG symbol={mt5_symbol} entry={sig.entry_price} tf={sig.tf} confirm_time_ms={sig.confirm_time_ms}")
        execute_long(mt5_symbol, sig.entry_price)

    def on_mt5_signal(sig: Signal):
        mt5_symbol = _map_symbol(cfg, sig.symbol)
        log.info(f"MT5 SIGNAL LONG symbol={mt5_symbol} entry={sig.entry_price} confirm_time_ms={sig.confirm_time_ms}")
        execute_long(mt5_symbol, sig.entry_price)

    async def run_tv_server():
        await start_server(
            host=cfg.tv_bridge.host,
            port=cfg.tv_bridge.port,
            path=cfg.tv_bridge.path,
            secret=cfg.tv_bridge.secret,
            require_tf_match=cfg.tv_bridge.require_tf_match,
            expected_tf=cfg.expected_tv_tf,
            on_signal=on_tv,
        )
        while True:
            await asyncio.sleep(3600)

    def run_mt5_master():
        engine = PineParityEngine(tf=cfg.timeframe, cfg=cfg.strategy)
        runner = MT5FeedRunner(bridge=bridge, engine=engine, timeframe=cfg.timeframe)
        syms = [_map_symbol(cfg, s) for s in cfg.symbols]
        runner.run_forever(syms, on_signal=on_mt5_signal, poll_seconds=1.0)

    try:
        mode = cfg.mode.lower().strip()
        if mode == "tv_master":
            if not cfg.tv_bridge.enabled:
                raise ValueError("mode=tv_master requires tv_bridge.enabled=true")
            log.info("Starting Mode A: TradingView webhook is master.")
            asyncio.run(run_tv_server())
        elif mode == "mt5_master":
            log.info("Starting Mode B: MT5 feed is master (Python parity engine).")
            run_mt5_master()
        else:
            raise ValueError(f"Unknown mode: {cfg.mode} (expected tv_master or mt5_master)")
        return 0
    finally:
        try:
            bridge.shutdown()
        except Exception:
            pass
