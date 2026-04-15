from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

from ...portal import get_portal_manager
from ..models import PortalChannelBinding
from .base import Connector

logger = logging.getLogger(__name__)

try:
    import lark_oapi as lark

    _LARK_AVAILABLE = True
except Exception:
    lark = None  # type: ignore[assignment]
    _LARK_AVAILABLE = False


class FeishuConnector(Connector):
    def __init__(self, binding: PortalChannelBinding):
        super().__init__(binding)
        self._ws_client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._http: Optional[aiohttp.ClientSession] = None
        self._tenant_token: Optional[str] = None
        self._tenant_token_expire_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._ws_task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        if not _LARK_AVAILABLE:
            raise RuntimeError("lark-oapi not installed")

        app_id = str(self.binding.config.get("app_id") or "").strip()
        app_secret = str(self.binding.config.get("app_secret") or "").strip()
        domain = str(self.binding.config.get("domain") or "https://open.feishu.cn").strip()
        if not app_id or not app_secret:
            raise RuntimeError("missing feishu app_id/app_secret")

        self._loop = asyncio.get_running_loop()
        self._http = aiohttp.ClientSession(trust_env=True)
        self._tenant_token = None
        self._tenant_token_expire_at = 0.0

        def on_message(data: Any) -> None:
            try:
                payload = json.loads(lark.JSON.marshal(data))  # type: ignore[union-attr]
            except Exception:
                logger.warning("[%s/%s] invalid event payload", self.binding.portal_id, self.binding.channel)
                return
            asyncio.run_coroutine_threadsafe(self._on_event(app_id, app_secret, domain, payload), self._loop)

        event_handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()  # type: ignore[union-attr]
        self._ws_client = lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=lark.LogLevel.ERROR, domain=domain)  # type: ignore[union-attr]
        try:
            await self._ensure_tenant_token(app_id, app_secret, domain)
            self.mark_success()
            self._ws_task = asyncio.create_task(asyncio.to_thread(self._ws_client.start))
            while self._running and self._ws_task and not self._ws_task.done():
                await asyncio.sleep(1)
            if self._ws_task and self._ws_task.done():
                exc = self._ws_task.exception()
                if exc:
                    raise exc
        finally:
            try:
                if self._ws_task and not self._ws_task.done():
                    stop_fn = getattr(self._ws_client, "stop", None)
                    if callable(stop_fn):
                        stop_fn()
            except Exception:
                pass
            if self._ws_task:
                self._ws_task.cancel()
                try:
                    await self._ws_task
                except Exception:
                    pass
                self._ws_task = None
            if self._http:
                await self._http.aclose()
                self._http = None
            self._ws_client = None

    async def stop(self) -> None:
        await super().stop()
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except Exception:
                pass
            self._ws_task = None
        if self._http:
            await self._http.aclose()
            self._http = None
        self._ws_client = None

    async def _on_event(self, app_id: str, app_secret: str, domain: str, payload: Dict[str, Any]) -> None:
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_id = (((sender.get("sender_id") or {}).get("open_id")) or "") if isinstance(sender, dict) else ""

        msg_type = str(message.get("message_type") or "")
        if msg_type != "text":
            return
        chat_id = str(message.get("chat_id") or "")
        if not chat_id:
            return
        content_raw = str(message.get("content") or "")
        try:
            content = json.loads(content_raw)
        except Exception:
            content = {}
        text = str(content.get("text") or "").strip()
        if not text:
            return

        portal_mgr = get_portal_manager()
        svc = await portal_mgr.get_service(self.binding.portal_id)
        if not svc:
            await self._send_text(app_id, app_secret, domain, chat_id, "Portal not found")
            return

        session_id = f"feishu:{self.binding.portal_id}:{chat_id}"
        reply = ""
        async for ev in svc.chat(session_id=session_id, user_message=text, user_id=sender_id or "default", stream=False):
            if ev.delta:
                reply += ev.delta
        reply = reply.strip() or "…"
        await self._send_text(app_id, app_secret, domain, chat_id, reply[:4000])

    async def _ensure_tenant_token(self, app_id: str, app_secret: str, domain: str) -> str:
        now = time.time()
        if self._tenant_token and now < self._tenant_token_expire_at:
            return self._tenant_token
        async with self._token_lock:
            now = time.time()
            if self._tenant_token and now < self._tenant_token_expire_at:
                return self._tenant_token
            if not self._http:
                raise RuntimeError("http client not ready")
            url = f"{domain.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal"
            async with self._http.post(url, json={"app_id": app_id, "app_secret": app_secret}) as resp:
                data = await resp.json()
            token = str(data.get("tenant_access_token") or "")
            if not token:
                raise RuntimeError("failed to get tenant_access_token")
            expires_in = int(data.get("expire") or data.get("expires_in") or 7200)
            self._tenant_token = token
            self._tenant_token_expire_at = time.time() + max(60, expires_in - 60)
            return token

    async def _send_text(self, app_id: str, app_secret: str, domain: str, chat_id: str, text: str) -> None:
        if not self._http:
            return
        url = f"{domain.rstrip('/')}/open-apis/im/v1/messages?receive_id_type=chat_id"
        body = {"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
        for _ in range(2):
            token = await self._ensure_tenant_token(app_id, app_secret, domain)
            async with self._http.post(url, json=body, headers={"Authorization": f"Bearer {token}"}) as resp:
                raw = await resp.text()
                if resp.status in (401, 403):
                    self._tenant_token = None
                    self._tenant_token_expire_at = 0.0
                    continue
                if resp.status >= 400:
                    self._last_error = f"feishu send HTTP {resp.status}"
                    logger.warning("[%s/%s] send failed: %s", self.binding.portal_id, self.binding.channel, raw[:200])
                else:
                    self.mark_success()
                return
