from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..storage.persistence import StorageBackend
from .models import ChannelName, PortalChannelBinding

BINDING_COLLECTION = "portal_channel_bindings"


def _binding_id(portal_id: str, channel: ChannelName) -> str:
    return f"{portal_id}:{channel}"


def _encode(binding: PortalChannelBinding) -> Dict[str, Any]:
    data = binding.model_dump()
    for k in ("created_at", "updated_at"):
        if isinstance(data.get(k), datetime):
            data[k] = data[k].isoformat()
    return data


def _decode(data: Dict[str, Any]) -> PortalChannelBinding:
    return PortalChannelBinding(**data)


class PortalChannelBindingStore:
    def __init__(self, backend: StorageBackend):
        self._backend = backend

    async def get(self, portal_id: str, channel: ChannelName) -> Optional[PortalChannelBinding]:
        data = await self._backend.load(BINDING_COLLECTION, _binding_id(portal_id, channel))
        return _decode(data) if data else None

    async def upsert(self, binding: PortalChannelBinding) -> PortalChannelBinding:
        binding.updated_at = datetime.utcnow()
        await self._backend.save(BINDING_COLLECTION, _binding_id(binding.portal_id, binding.channel), _encode(binding))
        return binding

    async def delete(self, portal_id: str, channel: ChannelName) -> bool:
        return await self._backend.delete(BINDING_COLLECTION, _binding_id(portal_id, channel))

    async def list_by_portal(self, portal_id: str) -> List[PortalChannelBinding]:
        items = await self._backend.list_all(BINDING_COLLECTION)
        out: List[PortalChannelBinding] = []
        for item in items:
            if item.get("portal_id") == portal_id:
                try:
                    out.append(_decode(item))
                except Exception:
                    continue
        return out

    async def list_all(self) -> List[PortalChannelBinding]:
        items = await self._backend.list_all(BINDING_COLLECTION)
        out: List[PortalChannelBinding] = []
        for item in items:
            try:
                out.append(_decode(item))
            except Exception:
                continue
        return out

