from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from ...portal import get_portal_manager
from ..models import PortalChannelBinding
from .base import Connector

logger = logging.getLogger(__name__)


class TelegramConnector(Connector):
    def __init__(self, binding: PortalChannelBinding):
        super().__init__(binding)
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset: int = int(binding.state.get("offset") or 0)

    async def run(self) -> None:
        token = str(self.binding.config.get("token") or "").strip()
        if not token:
            raise RuntimeError("missing telegram token")

        self._session = aiohttp.ClientSession(trust_env=True)
        try:
            me = await self._api(token, "getMe", {}, timeout=20)
            if not me.get("ok"):
                raise RuntimeError(str(me.get("description") or "telegram getMe failed"))
            bot = (me.get("result") or {}).get("username")
            if bot:
                self.mark_success({"bot": bot})
            while self._running:
                try:
                    updates = await self._api(
                        token,
                        "getUpdates",
                        {
                            "timeout": 30,
                            "allowed_updates": ["message"],
                            "offset": self._offset,
                        },
                        timeout=35,
                    )
                except Exception as e:
                    self._last_error = str(e)
                    await asyncio.sleep(2)
                    continue
                if not updates.get("ok"):
                    self._last_error = str(updates.get("description") or "telegram api error")
                    await asyncio.sleep(2)
                    continue
                self.mark_success()
                for u in updates.get("result") or []:
                    try:
                        update_id = int(u.get("update_id") or 0)
                        if update_id >= self._offset:
                            self._offset = update_id + 1
                        msg = u.get("message") or {}
                        text = (msg.get("text") or "").strip()
                        if not text:
                            continue
                        chat = msg.get("chat") or {}
                        chat_id = chat.get("id")
                        if chat_id is None:
                            continue
                        sender = msg.get("from") or {}
                        user_id = str(sender.get("id") or "default")
                        await self._handle_text(token, str(chat_id), user_id, text)
                    except Exception as e:
                        logger.warning("[%s/%s] handle update failed: %s", self.binding.portal_id, self.binding.channel, str(e))
                self.binding.state["offset"] = self._offset
        finally:
            await self._session.aclose()
            self._session = None

    async def _handle_text(self, token: str, chat_id: str, user_id: str, text: str) -> None:
        portal_mgr = get_portal_manager()
        svc = await portal_mgr.get_service(self.binding.portal_id)
        if not svc:
            await self._send_message(token, chat_id, "Portal not found")
            return

        session_id = f"telegram:{self.binding.portal_id}:{chat_id}"
        reply = ""
        async for event in svc.chat(session_id=session_id, user_message=text, user_id=user_id, stream=False):
            if event.delta:
                reply += event.delta
        reply = reply.strip() or "…"
        await self._send_message(token, chat_id, reply[:4000])

    async def _send_message(self, token: str, chat_id: str, text: str) -> None:
        await self._api(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": text},
            timeout=20,
        )

    async def _api(self, token: str, method: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("telegram session not ready")
        url = f"https://api.telegram.org/bot{token}/{method}"
        async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            raw = await resp.text()
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": False, "description": raw[:200]}
