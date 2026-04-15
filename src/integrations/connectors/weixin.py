from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

from ...portal import get_portal_manager
from ..models import PortalChannelBinding, WeixinQrStartResponse, WeixinQrStatusResponse
from .base import Connector

logger = logging.getLogger(__name__)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
ILINK_APP_ID = "ilinkai"
ILINK_APP_CLIENT_VERSION = 1
QR_SESSION_TTL_SECONDS = 600
QR_SESSION_MAX = 200


def _extract_text(item_list: List[Dict[str, Any]]) -> str:
    for item in item_list:
        if item.get("type") == 1:
            text = str((item.get("text_item") or {}).get("text") or "")
            return text
    for item in item_list:
        if item.get("type") == 4:
            voice_text = str((item.get("voice_item") or {}).get("text") or "")
            if voice_text:
                return voice_text
    return ""


async def _api_get(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    endpoint: str,
    timeout_ms: int,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint}"
    headers = {
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.get(url, headers=headers, timeout=timeout) as response:
        raw = await response.text()
        if not response.ok:
            raise RuntimeError(f"iLink GET {endpoint} HTTP {response.status}: {raw[:200]}")
        return json.loads(raw)


async def _api_post(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    endpoint: str,
    payload: Dict[str, Any],
    token: str,
    timeout_ms: int,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        "iLink-Bot-Token": token,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    async with session.post(url, headers=headers, json=payload, timeout=timeout) as response:
        raw = await response.text()
        if not response.ok:
            raise RuntimeError(f"iLink POST {endpoint} HTTP {response.status}: {raw[:200]}")
        return json.loads(raw)


class WeixinQrLoginManager:
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _gc(self) -> None:
        now = time.time()
        expired = [k for k, v in self._sessions.items() if now - float(v.get("created_at") or 0) > QR_SESSION_TTL_SECONDS]
        for k in expired:
            self._sessions.pop(k, None)
        if len(self._sessions) > QR_SESSION_MAX:
            keys = sorted(self._sessions.keys(), key=lambda k: float(self._sessions[k].get("created_at") or 0))
            for k in keys[: max(0, len(self._sessions) - QR_SESSION_MAX)]:
                self._sessions.pop(k, None)

    async def start(self) -> WeixinQrStartResponse:
        self._gc()
        login_id = str(int(time.time() * 1000)) + "-" + str(id(self))
        async with aiohttp.ClientSession(trust_env=True) as session:
            qr_resp = await _api_get(
                session,
                base_url=ILINK_BASE_URL,
                endpoint=f"{EP_GET_BOT_QR}?bot_type=3",
                timeout_ms=35_000,
            )
        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            raise RuntimeError("weixin QR missing qrcode")
        self._sessions[login_id] = {
            "qrcode": qrcode_value,
            "base_url": ILINK_BASE_URL,
            "created_at": time.time(),
        }
        return WeixinQrStartResponse(login_id=login_id, qrcode=qrcode_value, qrcode_img_content=qrcode_url)

    async def poll(self, login_id: str) -> WeixinQrStatusResponse:
        self._gc()
        s = self._sessions.get(login_id)
        if not s:
            return WeixinQrStatusResponse(login_id=login_id, status="expired")
        qrcode = str(s.get("qrcode") or "")
        base_url = str(s.get("base_url") or ILINK_BASE_URL)
        async with aiohttp.ClientSession(trust_env=True) as session:
            status_resp = await _api_get(
                session,
                base_url=base_url,
                endpoint=f"{EP_GET_QR_STATUS}?qrcode={qrcode}",
                timeout_ms=35_000,
            )
        status = str(status_resp.get("status") or "wait")
        if status == "scaned_but_redirect":
            redirect_host = str(status_resp.get("redirect_host") or "")
            if redirect_host:
                s["base_url"] = f"https://{redirect_host}"
            return WeixinQrStatusResponse(login_id=login_id, status="scaned")
        if status == "confirmed":
            account_id = str(status_resp.get("ilink_bot_id") or "")
            token = str(status_resp.get("bot_token") or "")
            user_id = str(status_resp.get("ilink_user_id") or "")
            baseurl = str(status_resp.get("baseurl") or s.get("base_url") or ILINK_BASE_URL)
            if account_id and token:
                cred = {
                    "account_id": account_id,
                    "token": token,
                    "base_url": baseurl,
                    "user_id": user_id,
                }
                self._sessions.pop(login_id, None)
                return WeixinQrStatusResponse(login_id=login_id, status="confirmed", credential=cred)
            self._sessions.pop(login_id, None)
            return WeixinQrStatusResponse(login_id=login_id, status="error")
        if status == "expired":
            self._sessions.pop(login_id, None)
            return WeixinQrStatusResponse(login_id=login_id, status="expired")
        if status == "scaned":
            return WeixinQrStatusResponse(login_id=login_id, status="scaned")
        return WeixinQrStatusResponse(login_id=login_id, status="wait")


class WeixinConnector(Connector):
    def __init__(self, binding: PortalChannelBinding):
        super().__init__(binding)
        self._session: Optional[aiohttp.ClientSession] = None
        self._sync_buf = str(binding.state.get("get_updates_buf") or "")

    async def run(self) -> None:
        token = str(self.binding.config.get("token") or "").strip()
        base_url = str(self.binding.config.get("base_url") or ILINK_BASE_URL).strip().rstrip("/")
        if not token:
            raise RuntimeError("missing weixin token")

        self._session = aiohttp.ClientSession(trust_env=True)
        try:
            backoff = [1, 2, 5, 10, 30]
            backoff_idx = 0
            while self._running:
                try:
                    resp = await _api_post(
                        self._session,
                        base_url=base_url,
                        endpoint=EP_GET_UPDATES,
                        payload={"get_updates_buf": self._sync_buf},
                        token=token,
                        timeout_ms=35_000,
                    )
                except asyncio.TimeoutError:
                    await asyncio.sleep(0.2)
                    continue
                except Exception as e:
                    self._last_error = str(e)
                    delay = backoff[min(backoff_idx, len(backoff) - 1)]
                    backoff_idx += 1
                    logger.warning("[%s/%s] getupdates failed, retry in %ss: %s", self.binding.portal_id, self.binding.channel, delay, self._last_error)
                    await asyncio.sleep(delay)
                    continue
                backoff_idx = 0
                self.mark_success()
                self._sync_buf = str(resp.get("get_updates_buf") or self._sync_buf)
                self.binding.state["get_updates_buf"] = self._sync_buf
                msgs = resp.get("msgs") or []
                if not isinstance(msgs, list):
                    await asyncio.sleep(1)
                    continue
                for msg in msgs:
                    try:
                        await self._handle_msg(token, base_url, msg)
                    except Exception as e:
                        logger.warning("[%s/%s] handle message failed: %s", self.binding.portal_id, self.binding.channel, str(e))
        finally:
            await self._session.aclose()
            self._session = None

    async def _handle_msg(self, token: str, base_url: str, msg: Dict[str, Any]) -> None:
        from_user_id = str(msg.get("from_user_id") or "")
        to_user_id = str(msg.get("to_user_id") or "")
        item_list = msg.get("item_list") or []
        if not from_user_id or not isinstance(item_list, list):
            return
        if str(msg.get("message_type") or "") != "2":
            return
        text = _extract_text(item_list).strip()
        if not text:
            return
        context_token = str(msg.get("context_token") or "")

        portal_mgr = get_portal_manager()
        svc = await portal_mgr.get_service(self.binding.portal_id)
        if not svc:
            await self._send_text(token, base_url, from_user_id, "Portal not found", context_token=context_token)
            return

        session_id = f"weixin:{self.binding.portal_id}:{from_user_id}"
        reply = ""
        async for event in svc.chat(session_id=session_id, user_message=text, user_id=from_user_id, stream=False):
            if event.delta:
                reply += event.delta
        reply = reply.strip() or "…"
        await self._send_text(token, base_url, from_user_id, reply[:3800], context_token=context_token)

    async def _send_text(self, token: str, base_url: str, to_user_id: str, text: str, context_token: str) -> None:
        if not self._session:
            return
        msg: Dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": "proton",
            "message_type": "2",
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        if context_token:
            msg["context_token"] = context_token
        await _api_post(
            self._session,
            base_url=base_url,
            endpoint=EP_SEND_MESSAGE,
            payload={"msg": msg},
            token=token,
            timeout_ms=20_000,
        )
