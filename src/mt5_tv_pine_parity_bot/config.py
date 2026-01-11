from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .telegram_notify import TelegramConfig

@dataclass
class TVBridgeConfig:
    enabled: bool
    host: str
    port: int
    path: str
    secret: str
    require_tf_match: bool = True

@dataclass
class MT5Config:
    login: int
    password: str
    server: str
    path: str = ""

@dataclass
class StrategyConfig:
    donLen: int = 120
    pivotLen: int = 5
    oscLen: int = 14
    extBandPct: float = 0.15

    tradeAllDivergences: bool = True

    longOnly: bool = True
    entryMode: str = "Confirm"   # Raw | Confirm
    minDivStrength: float = 15.0
    cooldownBars: int = 0

    useCvdGate: bool = True
    cvdLenMin: int = 60

    useDynamicCvdPct: bool = True
    cvdLookbackBars: int = 2880
    cvdPct: int = 75
    cvdThreshold: float = 244.075

    useBOSConfirm: bool = True
    bosAtrBuffer: float = 0.10
    maxWaitBars: int = 30

@dataclass
class RiskConfig:
    lot: float = 0.01
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    deviation: int = 20
    magic: int = 260110
    comment: str = "TV/MT5 PineParity LONG"

@dataclass
class TradeTrackerConfig:
    enabled: bool = False
    poll_seconds: float = 1.0
    history_days: int = 7

@dataclass
class BinanceConfig:
    venue: str = "spot"
    poll_seconds: float = 1.0
    limit: int = 500
    api_base: str = ""

@dataclass
class AppConfig:
    mode: str
    paper: bool
    tv_bridge: TVBridgeConfig
    mt5: MT5Config
    timeframe: str
    symbol_map: Dict[str, str]
    symbols: List[str]
    strategy: StrategyConfig
    risk: RiskConfig
    expected_tf: str = "15m"
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trade_tracker: TradeTrackerConfig = field(default_factory=TradeTrackerConfig)
    binance: BinanceConfig = field(default_factory=BinanceConfig)

def load_config(path: str) -> AppConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))

    # Backwards compatible: allow expected_tv_tf or expected_tf
    if isinstance(raw, dict):
        if "expected_tf" not in raw and "expected_tv_tf" in raw:
            raw["expected_tf"] = raw["expected_tv_tf"]

    tv = raw.get("tv_bridge", {}) or {}
    mt = raw.get("mt5", {}) or {}
    st = raw.get("strategy", {}) or {}
    rk = raw.get("risk", {}) or {}
    bn = raw.get("binance", {}) or {}
    tg = raw.get("telegram", {}) or {}
    tt = raw.get("trade_tracker", {}) or {}

    tvc = TVBridgeConfig(
        enabled=bool(tv.get("enabled", True)),
        host=str(tv.get("host", "0.0.0.0")),
        port=int(tv.get("port", 9001)),
        path=str(tv.get("path", "/tv")),
        secret=str(tv.get("secret", "")),
        require_tf_match=bool(tv.get("require_tf_match", True)),
    )
    mtc = MT5Config(
        login=int(mt.get("login", 0)),
        password=str(mt.get("password", "")),
        server=str(mt.get("server", "")),
        path=str(mt.get("path", "")),
    )
    sc = StrategyConfig(**st)
    rc = RiskConfig(**rk)
    notify_exit_val = tg.get("notify_exit", tg.get("notify_exits", True))
    tgc = TelegramConfig(
        enabled=bool(tg.get("enabled", False)),
        token=str(tg.get("token", "")),
        chat_id=str(tg.get("chat_id", "")),
        throttle_seconds=int(tg.get("throttle_seconds", 20)),
        notify_startup=bool(tg.get("notify_startup", True)),
        notify_entry=bool(tg.get("notify_entry", True)),
        notify_exit=bool(notify_exit_val),
        notify_failures=bool(tg.get("notify_failures", True)),
        notify_rejects=bool(tg.get("notify_rejects", True)),
        notify_stale_feed=bool(tg.get("notify_stale_feed", True)),
    )
    ttc = TradeTrackerConfig(
        enabled=bool(tt.get("enabled", False)),
        poll_seconds=float(tt.get("poll_seconds", 1.0)),
        history_days=int(tt.get("history_days", 7)),
    )
    bnc = BinanceConfig(
        venue=str(bn.get("venue", "spot")),
        poll_seconds=float(bn.get("poll_seconds", 1.0)),
        limit=int(bn.get("limit", 500)),
        api_base=str(bn.get("api_base", "")),
    )

    cfg = AppConfig(
        mode=str(raw.get("mode", "tv_master")),
        paper=bool(raw.get("paper", True)),
        tv_bridge=tvc,
        mt5=mtc,
        timeframe=str(raw.get("timeframe", "M15")),
        expected_tf=str(raw.get("expected_tf", "15m")),
        symbol_map=dict(raw.get("symbol_map", {}) or {}),
        symbols=list(raw.get("symbols", []) or []),
        strategy=sc,
        risk=rc,
        telegram=tgc,
        trade_tracker=ttc,
        binance=bnc,
    )

    if cfg.tv_bridge.enabled and not cfg.tv_bridge.secret:
        raise ValueError("tv_bridge.secret is required when tv_bridge.enabled=true")
    return cfg
