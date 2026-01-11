from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from aiohttp import web

from .utils.logger import setup_logger

log = setup_logger("tv_bridge")

@dataclass
class TVSignal:
    secret: str
    symbol: str
    side: str
    entry_price: Optional[float]
    confirm_time_ms: Optional[int]
    tf: Optional[str]
    raw: Dict[str, Any]

def _fval(d: Dict[str, Any], key: str) -> Optional[float]:
    if key not in d:
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None

def _ival(d: Dict[str, Any], key: str) -> Optional[int]:
    if key not in d:
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None

def parse_tv_signal(payload: Dict[str, Any]) -> TVSignal:
    secret = str(payload.get("secret", "")).strip()
    symbol = str(payload.get("symbol", "")).strip()
    side = str(payload.get("side", "")).strip().upper()
    entry_price = _fval(payload, "entry_price")
    if entry_price is None:
        entry_price = _fval(payload, "price")
    confirm_time_ms = _ival(payload, "confirm_time_ms")
    tf = payload.get("tf")
    tf = str(tf).strip() if tf is not None else None
    return TVSignal(
        secret=secret,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        confirm_time_ms=confirm_time_ms,
        tf=tf,
        raw=payload,
    )

async def start_server(
    host: str,
    port: int,
    path: str,
    secret: str,
    require_tf_match: bool,
    expected_tf: Optional[str],
    on_signal: Callable[[TVSignal], Awaitable[None]],
    on_reject: Optional[Callable[[str, Dict[str, Any], str], Awaitable[None]]] = None,
) -> web.AppRunner:
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        async def reject(reason: str, status: int, payload: Optional[Dict[str, Any]] = None, extra: Optional[Dict[str, Any]] = None):
            if on_reject is not None:
                try:
                    await on_reject(reason, payload or {}, request.remote or "")
                except Exception as e:
                    log.error(f"reject handler failed: {e}")
            body = {"ok": False, "error": reason}
            if extra:
                body.update(extra)
            return web.json_response(body, status=status)

        if request.method != "POST":
            return await reject("method_not_allowed", 405)
        try:
            payload = await request.json()
        except Exception:
            return await reject("invalid_json", 400)
        if not isinstance(payload, dict):
            return await reject("invalid_payload", 400)

        sig = parse_tv_signal(payload)
        if not sig.secret or sig.secret != secret:
            return await reject("bad_secret", 401, payload=payload)
        if sig.side != "LONG":
            return await reject("side_not_supported", 400, payload=payload)

        if require_tf_match and expected_tf and sig.tf and sig.tf != expected_tf:
            return await reject("tf_mismatch", 400, payload=payload, extra={"got": sig.tf, "expected": expected_tf})
        if require_tf_match and expected_tf and not sig.tf:
            return await reject("missing_tf", 400, payload=payload)

        await on_signal(sig)
        return web.json_response({"ok": True})

    app.router.add_post(path, handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info(f"TV bridge listening on http://{host}:{port}{path}")
    return runner
