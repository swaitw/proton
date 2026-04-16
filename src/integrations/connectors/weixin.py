from __future__ import annotations

import asyncio
import json
import logging
import time
import re
from typing import Any, Dict, List, Optional

import aiohttp

from ...portal import get_portal_manager
from ..models import PortalChannelBinding, WeixinQrStartResponse, WeixinQrStatusResponse
from ..runtime import ChatQueueManager, MessageDeduplicator, is_sender_allowed, try_pair_sender
from ..media_store import save_bytes
from ..tls import create_aiohttp_session
from .base import Connector
from .weixin_media import WEIXIN_CDN_BASE_URL, crypto_available, download_and_decrypt_media, prepare_weixin_upload

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
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")
_PLAIN_IMAGE_URL_RE = re.compile(r"(https?://\S+\.(?:png|jpg|jpeg|gif|webp))(?:\?\S+)?", re.IGNORECASE)


def _extract_text(item_list: List[Dict[str, Any]]) -> str:
    for item in item_list:
        if item.get("type") == 1:
            text = str((item.get("text_item") or {}).get("text") or "")
            return text
    for item in item_list:
        if item.get("type") in (3, 4):
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
        self._dedup = MessageDeduplicator()
        self._queues = ChatQueueManager()

    async def run(self) -> None:
        token = str(self.binding.config.get("token") or "").strip()
        base_url = str(self.binding.config.get("base_url") or ILINK_BASE_URL).strip().rstrip("/")
        if not token:
            raise RuntimeError("missing weixin token")

        self._session = create_aiohttp_session(self.binding.config)
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
                        await self._on_msg(token, base_url, msg)
                    except Exception as e:
                        logger.warning("[%s/%s] handle message failed: %s", self.binding.portal_id, self.binding.channel, str(e))
        finally:
            await self._session.close()
            self._session = None

    async def _on_msg(self, token: str, base_url: str, msg: Dict[str, Any]) -> None:
        from_user_id = str(msg.get("from_user_id") or "")
        to_user_id = str(msg.get("to_user_id") or "")
        item_list = msg.get("item_list") or []
        if not from_user_id or not isinstance(item_list, list):
            return
        if str(msg.get("message_type") or "") != "2":
            return
        msg_id = str(msg.get("msg_id") or msg.get("message_id") or "")
        if msg_id and self._dedup.seen(msg_id):
            return
        text = _extract_text(item_list).strip()
        media_notes: List[str] = []
        if self._session and self.binding.config.get("enable_media_receive", True) and crypto_available():
            cdn_base_url = str(self.binding.config.get("cdn_base_url") or WEIXIN_CDN_BASE_URL).strip().rstrip("/")
            for item in item_list:
                try:
                    t = int(item.get("type") or 0)
                except Exception:
                    continue
                if t not in (2, 3, 4, 5):
                    continue
                if t == 2:
                    media = (item.get("image_item") or {}).get("media") or {}
                    encrypted_query_param = str(media.get("encrypt_query_param") or "")
                    aes_key_b64 = str(media.get("aes_key") or (item.get("image_item") or {}).get("aeskey") or "")
                    raw = await download_and_decrypt_media(
                        self._session,
                        cdn_base_url=cdn_base_url,
                        encrypted_query_param=encrypted_query_param or None,
                        aes_key_b64=aes_key_b64 or None,
                        full_url=str(media.get("full_url") or "") or None,
                        timeout_seconds=60.0,
                    )
                    path = save_bytes(portal_id=self.binding.portal_id, channel="weixin", data=raw, suffix=".jpg", hint="image")
                    media_notes.append(f"[图片] {path}")
                elif t == 3:
                    voice_item = item.get("voice_item") or {}
                    media = voice_item.get("media") or {}
                    encrypted_query_param = str(media.get("encrypt_query_param") or "")
                    aes_key_b64 = str(media.get("aes_key") or "") or None
                    raw = await download_and_decrypt_media(
                        self._session,
                        cdn_base_url=cdn_base_url,
                        encrypted_query_param=encrypted_query_param or None,
                        aes_key_b64=aes_key_b64,
                        full_url=str(media.get("full_url") or "") or None,
                        timeout_seconds=90.0,
                    )
                    path = save_bytes(portal_id=self.binding.portal_id, channel="weixin", data=raw, suffix=".bin", hint="voice")
                    media_notes.append(f"[语音] {path}")
                elif t == 4:
                    file_item = item.get("file_item") or {}
                    if file_item:
                        media = file_item.get("media") or {}
                        encrypted_query_param = str(media.get("encrypt_query_param") or "")
                        aes_key_b64 = str(media.get("aes_key") or "") or None
                        raw = await download_and_decrypt_media(
                            self._session,
                            cdn_base_url=cdn_base_url,
                            encrypted_query_param=encrypted_query_param or None,
                            aes_key_b64=aes_key_b64,
                            full_url=str(media.get("full_url") or "") or None,
                            timeout_seconds=120.0,
                        )
                        filename = str(file_item.get("file_name") or "document.bin")
                        suffix = "." + filename.split(".")[-1] if "." in filename else ".bin"
                        path = save_bytes(portal_id=self.binding.portal_id, channel="weixin", data=raw, suffix=suffix, hint="file")
                        media_notes.append(f"[文件] {filename} {path}")
                    else:
                        voice_item = item.get("voice_item") or {}
                        media = voice_item.get("media") or {}
                        encrypted_query_param = str(media.get("encrypt_query_param") or "")
                        aes_key_b64 = str(media.get("aes_key") or "") or None
                        raw = await download_and_decrypt_media(
                            self._session,
                            cdn_base_url=cdn_base_url,
                            encrypted_query_param=encrypted_query_param or None,
                            aes_key_b64=aes_key_b64,
                            full_url=str(media.get("full_url") or "") or None,
                            timeout_seconds=90.0,
                        )
                        path = save_bytes(portal_id=self.binding.portal_id, channel="weixin", data=raw, suffix=".bin", hint="voice")
                        media_notes.append(f"[语音] {path}")
                elif t == 5:
                    media_notes.append("[视频]")
                else:
                    continue
                self.mark_success()
        if media_notes:
            text = (text + "\n" + "\n".join(media_notes)).strip()
        if not text:
            return
        context_token = str(msg.get("context_token") or "")

        if not is_sender_allowed(self.binding.config, from_user_id):
            pairing = try_pair_sender(self.binding.config, from_user_id, text)
            if pairing.paired:
                await self._send_text(token, base_url, from_user_id, "已配对成功，可以开始对话。", context_token=context_token)
            else:
                await self._send_text(token, base_url, from_user_id, "未授权。请发送配对码完成绑定。", context_token=context_token)
            return

        await self._queues.enqueue(
            from_user_id,
            lambda: self._handle_text(token, base_url, from_user_id, text, context_token),
        )

    async def _handle_text(self, token: str, base_url: str, from_user_id: str, text: str, context_token: str) -> None:
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
        if self.binding.config.get("enable_media_send", True) and crypto_available() and self._session:
            url = ""
            m = _MARKDOWN_IMAGE_RE.search(reply)
            if m:
                url = m.group(1)
            else:
                m2 = _PLAIN_IMAGE_URL_RE.search(reply)
                if m2:
                    url = m2.group(1)
            if url:
                try:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        resp.raise_for_status()
                        raw = await resp.read()
                    cdn_base_url = str(self.binding.config.get("cdn_base_url") or WEIXIN_CDN_BASE_URL).strip().rstrip("/")
                    up = await prepare_weixin_upload(
                        self._session,
                        base_url=base_url,
                        token=token,
                        to_user_id=from_user_id,
                        plaintext=raw,
                        filename=url.split("?", 1)[0].split("/")[-1] or "image.jpg",
                        cdn_base_url=cdn_base_url,
                    )
                    await self._send_media(token, base_url, from_user_id, up["item"], context_token=context_token)
                    cleaned = reply.replace(url, "").strip()
                    cleaned = _MARKDOWN_IMAGE_RE.sub("", cleaned).strip()
                    if cleaned:
                        await self._send_text(token, base_url, from_user_id, cleaned[:3800], context_token=context_token)
                    return
                except Exception as e:
                    logger.warning("[%s/%s] send media failed: %s", self.binding.portal_id, self.binding.channel, str(e))
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

    async def _send_media(self, token: str, base_url: str, to_user_id: str, media_item: Dict[str, Any], context_token: str) -> None:
        if not self._session:
            return
        msg: Dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": "proton",
            "message_type": "2",
            "message_state": 2,
            "item_list": [media_item],
        }
        if context_token:
            msg["context_token"] = context_token
        await _api_post(
            self._session,
            base_url=base_url,
            endpoint=EP_SEND_MESSAGE,
            payload={"msg": msg},
            token=token,
            timeout_ms=40_000,
        )
