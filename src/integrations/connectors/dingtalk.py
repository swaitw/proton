from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional

import httpx

from ...portal import get_portal_manager
from ..models import PortalChannelBinding
from .base import Connector

logger = logging.getLogger(__name__)

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotHandler, ChatbotMessage

    _DINGTALK_AVAILABLE = True
except Exception:
    dingtalk_stream = None  # type: ignore[assignment]
    ChatbotHandler = object  # type: ignore[assignment]
    ChatbotMessage = object  # type: ignore[assignment]
    _DINGTALK_AVAILABLE = False

_DINGTALK_WEBHOOK_RE = re.compile(r"^https://api\.dingtalk\.com/")


class DingTalkConnector(Connector):
    def __init__(self, binding: PortalChannelBinding):
        super().__init__(binding)
        self._client: Any = None
        self._http: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session_webhooks: Dict[str, str] = {}

    async def run(self) -> None:
        if not _DINGTALK_AVAILABLE:
            raise RuntimeError("dingtalk-stream not installed")

        client_id = str(self.binding.config.get("client_id") or "").strip()
        client_secret = str(self.binding.config.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise RuntimeError("missing dingtalk client_id/client_secret")

        self._http = httpx.AsyncClient(timeout=30.0)
        self._loop = asyncio.get_running_loop()
        credential = dingtalk_stream.Credential(client_id, client_secret)
        self._client = dingtalk_stream.DingTalkStreamClient(credential)
        self._client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, _IncomingHandler(self, self._loop))

        try:
            while self._running:
                try:
                    await asyncio.to_thread(self._client.start)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    self._last_error = str(e)
                    logger.warning("[%s/%s] stream client error: %s", self.binding.portal_id, self.binding.channel, self._last_error)
                    await asyncio.sleep(3)
                else:
                    logger.warning("[%s/%s] stream client disconnected cleanly, reconnecting in 3s...", self.binding.portal_id, self.binding.channel)
                    await asyncio.sleep(3)
        finally:
            if self._http:
                await self._http.aclose()
                self._http = None

    async def stop(self) -> None:
        await super().stop()
        if self._http:
            await self._http.aclose()
            self._http = None
        self._client = None
        self._session_webhooks.clear()

    async def _on_message(self, message: Any) -> None:
        text = str(getattr(message, "text", "") or "")
        if not text.strip():
            return

        conversation_id = str(getattr(message, "conversation_id", "") or "")
        sender_id = str(getattr(message, "sender_id", "") or "")
        chat_id = conversation_id or sender_id
        if not chat_id:
            return

        session_webhook = str(getattr(message, "session_webhook", "") or "")
        if session_webhook and _DINGTALK_WEBHOOK_RE.match(session_webhook):
            self._session_webhooks[chat_id] = session_webhook
            self.mark_success()

        portal_mgr = get_portal_manager()
        svc = await portal_mgr.get_service(self.binding.portal_id)
        if not svc:
            await self._send(chat_id, "Portal not found")
            return

        session_id = f"dingtalk:{self.binding.portal_id}:{chat_id}"
        reply = ""
        async for event in svc.chat(session_id=session_id, user_message=text, user_id=sender_id or "default", stream=False):
            if event.delta:
                reply += event.delta
        reply = reply.strip() or "…"
        await self._send(chat_id, reply[:19000])

    async def _send(self, chat_id: str, text: str) -> None:
        if not self._http:
            return
        webhook = self._session_webhooks.get(chat_id)
        if not webhook:
            return
        try:
            await self._http.post(
                webhook,
                json={
                    "msgtype": "markdown",
                    "markdown": {"title": "Proton", "text": text},
                },
            )
            self.mark_success()
        except Exception as e:
            self._last_error = str(e)
            logger.warning("[%s/%s] send failed: %s", self.binding.portal_id, self.binding.channel, self._last_error)


class _IncomingHandler(ChatbotHandler):  # type: ignore[misc]
    def __init__(self, connector: DingTalkConnector, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._connector = connector
        self._loop = loop

    def process(self, callback: Any) -> Any:
        try:
            message = ChatbotMessage.from_dict(callback.data)  # type: ignore[attr-defined]
        except Exception:
            return callback.response()
        asyncio.run_coroutine_threadsafe(self._connector._on_message(message), self._loop)
        return callback.response()
