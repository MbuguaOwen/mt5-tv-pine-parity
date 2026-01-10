from __future__ import annotations

from datetime import datetime, timezone

def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()

def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
