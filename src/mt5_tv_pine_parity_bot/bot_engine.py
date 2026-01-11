from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import MetaTrader5 as mt5

from .config import load_config, AppConfig
from .binance_feed import BinanceFeedRunner, BinanceSignal
from .mt5_bridge import MT5Bridge
from .strategy_engine import PineParityEngine
from .telegram_notify import TelegramNotifier
from .trade_tracker import TradeTracker, TradeMeta
from .tv_bridge import TVSignal, start_server
from .timeframes import mt5_tf
from .utils.logger import setup_logger

log = setup_logger("engine")


def _map_symbol(cfg: AppConfig, symbol: str) -> str:
    # TV/Binance symbol -> MT5 symbol, otherwise passthrough
    return cfg.symbol_map.get(symbol, symbol)


def _calc_sl_tp_long(entry: float, atr: Optional[float], sl_mult: float, tp_mult: float):
    if atr is None or atr != atr or atr <= 0:
        return None, None
    sl = entry - atr * sl_mult
    tp = entry + atr * tp_mult
    return sl, tp


def _effective_expected_tf(cfg: AppConfig) -> Optional[str]:
    """
    Supports both config keys:
      - expected_tf (new)
      - expected_tv_tf (legacy)
    """
    v = getattr(cfg, "expected_tf", None)
    if isinstance(v, str):
        v = v.strip() or None

    if not v:
        v2 = getattr(cfg, "expected_tv_tf", None)
        if isinstance(v2, str):
            v2 = v2.strip() or None
        v = v2

    return v


def run(config_path: str) -> int:
    cfg = load_config(config_path)

    bridge = MT5Bridge(cfg.mt5.login, cfg.mt5.password, cfg.mt5.server, cfg.mt5.path)
    bridge.connect()

    tg = TelegramNotifier(cfg.telegram)
    tracker = TradeTracker(
        tg,
        enabled=cfg.trade_tracker.enabled,
        poll_seconds=cfg.trade_tracker.poll_seconds,
        history_days=cfg.trade_tracker.history_days,
        magic=cfg.risk.magic,
    )
    tracker.start()

    last_tv_confirm: Dict[str, int] = {}
    last_binance_confirm: Dict[str, int] = {}

    # Compute expected TF once, log it once (so you KNOW what the service is enforcing)
    expected_tf = _effective_expected_tf(cfg)
    log.info(f"Config: mode={cfg.mode} paper={cfg.paper} require_tf_match={cfg.tv_bridge.require_tf_match} expected_tf={expected_tf}")
    if cfg.telegram.enabled and cfg.telegram.notify_startup:
        tg.send(
            f"BOT ONLINE\nMode: {cfg.mode}\nPaper: {cfg.paper}\nTF: {cfg.timeframe}\nSymbols: {', '.join(cfg.symbols)}",
            key="startup",
        )

    def _atr_hint(symbol: str) -> Optional[float]:
        try:
            import pandas as pd
            from .strategy_engine import PineParityEngine as PPE

            rates = bridge.copy_rates(symbol, mt5_tf(cfg.timeframe), 250)
            df = pd.DataFrame(rates)
            if len(df) > 50:
                atr_series = PPE._atr(df, 14)
                # last closed bar = -2
                return float(atr_series.iloc[-2])
        except Exception:
            return None
        return None

    def execute_long(
        symbol: str,
        entry_price: Optional[float],
        *,
        atr_hint: Optional[float],
        confirm_time_ms: Optional[int],
        source: str,
        tf: Optional[str],
    ):
        if entry_price is None:
            log.error(f"Cannot execute: missing entry_price for {symbol}")
            if cfg.telegram.enabled and cfg.telegram.notify_failures:
                tg.send(f"EXEC FAIL\nMode: {cfg.mode}\n{symbol} LONG\nmissing entry_price", key=f"fail:{symbol}")
            return

        if bridge.has_open_position(symbol, cfg.risk.magic):
            msg = f"SKIP already in position\n{symbol} LONG"
            log.warning(msg)
            if cfg.telegram.enabled and cfg.telegram.notify_failures:
                tg.send(msg, key=f"skip:{symbol}")
            return

        atr = atr_hint if atr_hint is not None else _atr_hint(symbol)
        sl, tp = _calc_sl_tp_long(
            entry=float(entry_price),
            atr=atr,
            sl_mult=cfg.risk.sl_atr_mult,
            tp_mult=cfg.risk.tp_atr_mult,
        )

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
            risk_ccy = None
            try:
                if sl is not None:
                    risk_ccy = abs(
                        bridge.order_calc_profit(
                            mt5.ORDER_TYPE_BUY,
                            symbol,
                            cfg.risk.lot,
                            float(entry_price),
                            float(sl),
                        )
                    )
            except Exception as e:
                log.warning(f"risk calc failed: {e}")

            meta = TradeMeta(
                mode=cfg.mode,
                source=source,
                symbol=symbol,
                tf=tf or cfg.timeframe,
                side="LONG",
                lot=float(cfg.risk.lot),
                entry_price=float(entry_price),
                sl=sl,
                tp=tp,
                confirm_time_ms=confirm_time_ms,
                magic=int(cfg.risk.magic),
                comment=str(cfg.risk.comment),
                risk_ccy=risk_ccy,
            )
            tracker.register_open(meta)

            if cfg.telegram.enabled and cfg.telegram.notify_entry:
                tg.send(
                    "ENTRY\n"
                    f"Mode: {meta.mode} | Source: {meta.source}\n"
                    f"{symbol} LONG tf={meta.tf}\n"
                    f"Entry: {meta.entry_price:.5f}\n"
                    f"SL:    {(sl if sl is not None else 0.0):.5f}\n"
                    f"TP:    {(tp if tp is not None else 0.0):.5f}\n"
                    f"Lot:   {meta.lot}\n"
                    f"Risk:  {(risk_ccy if risk_ccy is not None else 0.0):.2f}\n",
                    key=f"entry:{symbol}",
                )
        else:
            log.error(f"EXEC FAIL symbol={symbol} retcode={res.retcode} comment={res.comment}")
            if cfg.telegram.enabled and cfg.telegram.notify_failures:
                tg.send(
                    f"EXEC FAIL\nMode: {cfg.mode}\n{symbol} LONG\nretcode={res.retcode}\n{res.comment}",
                    key=f"fail:{symbol}",
                )

    async def on_tv(sig: TVSignal):
        mt5_symbol = _map_symbol(cfg, sig.symbol)

        # Dedupe on confirm_time_ms per-symbol (prevents double-fires)
        if sig.confirm_time_ms is not None:
            prev = last_tv_confirm.get(mt5_symbol)
            if prev is not None and sig.confirm_time_ms == prev:
                log.info(f"TV dedupe ignored symbol={mt5_symbol} confirm_time_ms={sig.confirm_time_ms}")
                return
            last_tv_confirm[mt5_symbol] = sig.confirm_time_ms

        log.info(f"TV SIGNAL LONG symbol={mt5_symbol} entry={sig.entry_price} tf={sig.tf} confirm_time_ms={sig.confirm_time_ms}")
        execute_long(
            mt5_symbol,
            sig.entry_price,
            atr_hint=None,
            confirm_time_ms=sig.confirm_time_ms,
            source="TV",
            tf=sig.tf or expected_tf or cfg.timeframe,
        )

    def on_binance_signal(out: BinanceSignal):
        sig = out.signal
        mt5_symbol = _map_symbol(cfg, sig.symbol)
        if sig.confirm_time_ms is not None:
            prev = last_binance_confirm.get(mt5_symbol)
            if prev is not None and sig.confirm_time_ms == prev:
                log.info(f"BINANCE dedupe ignored symbol={mt5_symbol} confirm_time_ms={sig.confirm_time_ms}")
                return
            last_binance_confirm[mt5_symbol] = sig.confirm_time_ms
        log.info(
            f"BINANCE SIGNAL LONG symbol={mt5_symbol} entry={sig.entry_price} confirm_time_ms={sig.confirm_time_ms}"
        )
        execute_long(
            mt5_symbol,
            sig.entry_price,
            atr_hint=out.atr,
            confirm_time_ms=sig.confirm_time_ms,
            source="BINANCE",
            tf=cfg.timeframe,
        )

    async def run_tv_server():
        async def on_reject(reason: str, payload: Dict[str, Any], ip: str):
            if not cfg.telegram.enabled or not cfg.telegram.notify_rejects:
                return
            summary_keys = ["symbol", "side", "tf", "confirm_time_ms", "entry_price", "price"]
            parts = []
            for k in summary_keys:
                if k in payload:
                    parts.append(f"{k}={payload.get(k)}")
            suffix = f" payload={','.join(parts)}" if parts else ""
            msg = f"REJECT reason={reason} ip={ip}{suffix}"
            tg.send(msg, key=f"reject:{reason}")

        await start_server(
            host=cfg.tv_bridge.host,
            port=cfg.tv_bridge.port,
            path=cfg.tv_bridge.path,
            secret=cfg.tv_bridge.secret,
            require_tf_match=cfg.tv_bridge.require_tf_match,
            expected_tf=expected_tf,
            on_signal=on_tv,
            on_reject=on_reject,
        )
        while True:
            await asyncio.sleep(3600)

    def run_binance_master():
        engine = PineParityEngine(tf=cfg.timeframe, cfg=cfg.strategy)
        runner = BinanceFeedRunner(cfg=cfg.binance, engine=engine, timeframe=cfg.timeframe)
        syms = [str(s).upper() for s in cfg.symbols]
        runner.run_forever(syms, on_signal=on_binance_signal, poll_seconds=cfg.binance.poll_seconds)

    try:
        mode = cfg.mode.lower().strip()
        if mode == "mt5_master":
            log.warning("mode=mt5_master is deprecated; use binance_master instead")
            mode = "binance_master"
        if mode == "tv_master":
            if not cfg.tv_bridge.enabled:
                raise ValueError("mode=tv_master requires tv_bridge.enabled=true")
            log.info("Starting Mode A: TradingView webhook is master.")
            asyncio.run(run_tv_server())
        elif mode == "binance_master":
            log.info("Starting Mode B: Binance feed is master (Python parity engine).")
            run_binance_master()
        else:
            raise ValueError(f"Unknown mode: {cfg.mode} (expected tv_master or binance_master)")
        return 0
    finally:
        try:
            tracker.stop()
            bridge.shutdown()
        except Exception:
            pass
