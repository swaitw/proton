from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from ..storage import get_storage_manager, initialize_storage
from .connectors import DingTalkConnector, FeishuConnector, TelegramConnector, WeixinConnector
from .connectors.weixin import WeixinQrLoginManager
from .models import ChannelName, PortalChannelBinding, PortalChannelStatus, WeixinQrStartResponse, WeixinQrStatusResponse
from .store import PortalChannelBindingStore


class IntegrationsGateway:
    def __init__(self):
        self._store: Optional[PortalChannelBindingStore] = None
        self._connectors: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._persist_task: Optional[asyncio.Task] = None
        self._weixin_qr = WeixinQrLoginManager()

    async def _ensure_ready(self) -> None:
        if self._store:
            return
        await initialize_storage()
        mgr = get_storage_manager()
        self._store = PortalChannelBindingStore(mgr.backend)

    def _key(self, portal_id: str, channel: ChannelName) -> str:
        return f"{portal_id}:{channel}"

    async def startup(self) -> None:
        await self._ensure_ready()
        async with self._lock:
            bindings = await self._store.list_all() if self._store else []
            for b in bindings:
                if b.enabled:
                    await self._connect_locked(b)
            if not self._persist_task:
                self._persist_task = asyncio.create_task(self._persist_loop())

    async def shutdown(self) -> None:
        async with self._lock:
            for c in list(self._connectors.values()):
                try:
                    await c.stop()
                except Exception:
                    continue
            self._connectors.clear()
            if self._persist_task:
                self._persist_task.cancel()
                try:
                    await self._persist_task
                except asyncio.CancelledError:
                    pass
                self._persist_task = None

    async def list_status(self, portal_id: str) -> Dict[str, PortalChannelStatus]:
        await self._ensure_ready()
        bindings = await self._store.list_by_portal(portal_id) if self._store else []
        out: Dict[str, PortalChannelStatus] = {}
        async with self._lock:
            for b in bindings:
                k = self._key(b.portal_id, b.channel)
                conn = self._connectors.get(k)
                if conn:
                    out[b.channel] = conn.status()
                else:
                    out[b.channel] = PortalChannelStatus(
                        portal_id=b.portal_id,
                        channel=b.channel,
                        enabled=b.enabled,
                        connected=False,
                        last_error=None,
                        meta={},
                    )
        return out

    async def upsert_binding(self, portal_id: str, channel: ChannelName, config: Dict[str, Any], enabled: bool = True) -> PortalChannelBinding:
        await self._ensure_ready()
        if not self._store:
            raise RuntimeError("storage not ready")
        existing = await self._store.get(portal_id, channel)
        binding = existing or PortalChannelBinding(portal_id=portal_id, channel=channel)
        binding.enabled = enabled
        binding.config = dict(config)
        binding.updated_at = datetime.utcnow()
        binding = await self._store.upsert(binding)
        async with self._lock:
            await self._disconnect_locked(portal_id, channel)
            if binding.enabled:
                await self._connect_locked(binding)
        return binding

    async def delete_binding(self, portal_id: str, channel: ChannelName) -> bool:
        await self._ensure_ready()
        if not self._store:
            return False
        async with self._lock:
            await self._disconnect_locked(portal_id, channel)
        return await self._store.delete(portal_id, channel)

    async def weixin_qr_start(self) -> WeixinQrStartResponse:
        return await self._weixin_qr.start()

    async def weixin_qr_poll(self, login_id: str) -> WeixinQrStatusResponse:
        return await self._weixin_qr.poll(login_id)

    async def _connect_locked(self, binding: PortalChannelBinding) -> None:
        k = self._key(binding.portal_id, binding.channel)
        if k in self._connectors:
            return
        if binding.channel == "telegram":
            conn = TelegramConnector(binding)
        elif binding.channel == "dingtalk":
            conn = DingTalkConnector(binding)
        elif binding.channel == "weixin":
            conn = WeixinConnector(binding)
        elif binding.channel == "feishu":
            conn = FeishuConnector(binding)
        else:
            return
        await conn.start()
        self._connectors[k] = conn

    async def _disconnect_locked(self, portal_id: str, channel: ChannelName) -> None:
        k = self._key(portal_id, channel)
        conn = self._connectors.pop(k, None)
        if conn:
            await conn.stop()

    async def _persist_loop(self) -> None:
        while True:
            await asyncio.sleep(20)
            try:
                await self._persist_states()
            except Exception:
                continue

    async def _persist_states(self) -> None:
        await self._ensure_ready()
        if not self._store:
            return
        async with self._lock:
            for conn in self._connectors.values():
                try:
                    await self._store.upsert(conn.binding)
                except Exception:
                    continue


_gateway: Optional[IntegrationsGateway] = None


def get_integrations_gateway() -> IntegrationsGateway:
    global _gateway
    if _gateway is None:
        _gateway = IntegrationsGateway()
    return _gateway

