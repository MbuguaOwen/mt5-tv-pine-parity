from __future__ import annotations

import MetaTrader5 as mt5

_UNIT_MAP = {
    "M": "M",
    "H": "H",
    "D": "D",
    "W": "W",
}

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

TF_SECONDS = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M4": 240,
    "M5": 300,
    "M6": 360,
    "M10": 600,
    "M12": 720,
    "M15": 900,
    "M20": 1200,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H3": 10800,
    "H4": 14400,
    "H6": 21600,
    "H8": 28800,
    "H12": 43200,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,  # approximate
}

def _normalize_mt5_key(tf: str) -> str:
    s = tf.strip().upper()
    if s in TF_MAP:
        return s
    if len(s) >= 2 and s[-1] in _UNIT_MAP and s[:-1].isdigit():
        unit = _UNIT_MAP[s[-1]]
        return f"{unit}{int(s[:-1])}"
    raise ValueError(f"Unsupported timeframe: {tf}")

def mt5_tf(tf: str) -> int:
    key = _normalize_mt5_key(tf)
    if key not in TF_MAP:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return TF_MAP[key]

def tf_seconds(tf: str) -> int:
    key = _normalize_mt5_key(tf)
    if key not in TF_SECONDS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    return TF_SECONDS[key]

def to_binance_interval(tf: str) -> str:
    s = tf.strip()
    if not s:
        raise ValueError("timeframe is required")
    s_upper = s.upper()
    if s_upper in TF_MAP:
        if s_upper.startswith("M"):
            return f"{int(s_upper[1:])}m"
        if s_upper.startswith("H"):
            return f"{int(s_upper[1:])}h"
        if s_upper.startswith("D"):
            return f"{int(s_upper[1:])}d"
        if s_upper.startswith("W"):
            return f"{int(s_upper[1:])}w"
    if s[-1].lower() in ("m", "h", "d", "w") and s[:-1].isdigit():
        return f"{int(s[:-1])}{s[-1].lower()}"
    raise ValueError(f"Unsupported binance timeframe: {tf}")
