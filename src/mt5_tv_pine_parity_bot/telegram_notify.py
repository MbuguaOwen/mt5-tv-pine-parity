from __future__ import annotations

import asyncio
import os
import ssl
import time
from dataclasses import dataclass
from typing import Dict, Optional

import aiohttp

from .utils.logger import setup_logger

log = setup_logger("telegram")


@dataclass
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    chat_id: str = ""
    throttle_seconds: int = 20

    notify_startup: bool = True
    notify_failures: bool = True
    notify_entry: bool = True
    notify_exit: bool = True
    notify_rejects: bool = True
    notify_stale_feed: bool = True


class TelegramNotifier:
    def __init__(self, cfg: TelegramConfig):
        self.cfg = cfg
        self._last_sent: Dict[str, float] = {}

    def _throttled(self, key: str) -> bool:
        if not self.cfg.throttle_seconds or self.cfg.throttle_seconds <= 0:
            return False
        now = time.time()
        last = self._last_sent.get(key, 0.0)
        if now - last < float(self.cfg.throttle_seconds):
            return True
        self._last_sent[key] = now
        return False

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        cafile = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
        if cafile:
            return ssl.create_default_context(cafile=cafile)
        return None

    async def _send_async(self, text: str) -> None:
        if not self.cfg.enabled:
            return
        if not self.cfg.token or not self.cfg.chat_id:
            log.warning("telegram not configured (missing token/chat_id)")
            return

        msg = str(text)
        if not msg.strip():
            log.warning("telegram message is empty")
            return

        url = f"https://api.telegram.org/bot{self.cfg.token}/sendMessage"
        payload = {
            "chat_id": self.cfg.chat_id,
            "text": msg,
            "disable_web_page_preview": True,
        }

        try:
            ssl_ctx = self._ssl_context()
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, ssl=ssl_ctx, timeout=10) as resp:
                    if resp.status >= 300:
                        body = await resp.text()
                        log.error(f"send failed status={resp.status} body={body[:200]}")
        except Exception as e:
            log.error(f"send failed: {e}")

    def send(self, text: str, *, key: Optional[str] = None) -> None:
        if not self.cfg.enabled:
            return
        if not text or not str(text).strip():
            return

        k = key or "msg"
        if self._throttled(k):
            return

        try:
            asyncio.run(self._send_async(text))
        except RuntimeError:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._send_async(text))
            except Exception as e:
                log.error(f"send failed (loop): {e}")
        except Exception as e:
            log.error(f"send failed: {e}")
