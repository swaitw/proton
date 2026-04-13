from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from ..core.models import PortalMemoryEntry
from .memory_provider import PortalMemoryProvider
from .mempalace_client import MemPalaceClient

logger = logging.getLogger(__name__)


class MemPalaceMemoryProvider(PortalMemoryProvider):
    def __init__(
        self,
        *,
        client: MemPalaceClient,
        wing_strategy: str = "per_user",
        default_room: str = "general",
    ):
        self._client = client
        self._wing_strategy = wing_strategy
        self._default_room = default_room

    async def retrieve(
        self,
        *,
        portal_id: str,
        user_id: str,
        query: str,
        top_k: int,
        include_global: bool,
        session_id: Optional[str],
    ) -> List[PortalMemoryEntry]:
        room = self._client.build_room(portal_id, self._default_room)
        search_tool = self._client.resolve_tool_name("mempalace_search")
        if not search_tool:
            return []
        wings = [self._client.build_wing(portal_id, user_id, self._wing_strategy)]
        if include_global and (self._wing_strategy or "").strip().lower() == "per_portal":
            wings.append(self._client.build_wing(portal_id, user_id, "per_user"))
        results: List[PortalMemoryEntry] = []
        seen: set[str] = set()
        for wing in wings:
            resp = await self._client.call(
                search_tool,
                {"query": query, "limit": int(top_k), "wing": wing, "room": room},
            )
            entries = self._parse_search_response(resp, portal_id=portal_id, user_id=user_id)
            for e in entries:
                key = f"{e.id}:{e.content}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(e)
        return results[: max(0, int(top_k))]

    async def delete(self, *, portal_id: str, user_id: str, entry_id: str) -> None:
        """Delete a single memory entry."""
        wing = self._client.build_wing(portal_id, user_id, self._wing_strategy)
        room = self._client.build_room(portal_id, self._default_room)
        
        delete_tool = self._client.resolve_tool_name("mempalace_delete_drawer")
        if not delete_tool:
            return
        await self._client.call(delete_tool, {"wing": wing, "room": room, "drawer_id": entry_id})

    async def clear(self, *, portal_id: str, user_id: str) -> None:
        """Clear all memories for a user in this portal."""
        wing = self._client.build_wing(portal_id, user_id, self._wing_strategy)
        room = self._client.build_room(portal_id, self._default_room)
        
        clear_tool = self._client.resolve_tool_name("mempalace_clear_room")
        if clear_tool:
            await self._client.call(clear_tool, {"wing": wing, "room": room})
            return
            
        search_tool = self._client.resolve_tool_name("mempalace_search")
        if not search_tool:
            return
        resp = await self._client.call(
            search_tool,
            {"query": "*", "limit": 2000, "wing": wing, "room": room},
        )
        entries = self._parse_search_response(resp, portal_id=portal_id, user_id=user_id)
        sem = asyncio.Semaphore(8)

        async def _del(entry: PortalMemoryEntry) -> None:
            async with sem:
                try:
                    await self.delete(portal_id=portal_id, user_id=user_id, entry_id=entry.id)
                except Exception:
                    return

        await asyncio.gather(*[_del(e) for e in entries])

    async def bounded_snapshot(
        self,
        *,
        portal_id: str,
        user_id: str,
        max_chars: int = 2000,
        max_entries: int = 20,
        include_global: bool = False,
    ) -> str:
        room = self._client.build_room(portal_id, self._default_room)
        search_tool = self._client.resolve_tool_name("mempalace_search")
        if not search_tool:
            return ""
        wings = [self._client.build_wing(portal_id, user_id, self._wing_strategy)]
        if include_global and (self._wing_strategy or "").strip().lower() == "per_portal":
            wings.append(self._client.build_wing(portal_id, user_id, "per_user"))
        entries: List[PortalMemoryEntry] = []
        seen: set[str] = set()
        for wing in wings:
            resp = await self._client.call(
                search_tool,
                {
                    "query": "critical facts preferences",
                    "limit": int(max_entries),
                    "wing": wing,
                    "room": room,
                },
            )
            for e in self._parse_search_response(resp, portal_id=portal_id, user_id=user_id):
                key = f"{e.id}:{e.content}"
                if key in seen:
                    continue
                seen.add(key)
                entries.append(e)

        block = "\n".join(f"- {e.content}" for e in entries[: max(0, int(max_entries))])
        if max_chars > 0 and len(block) > max_chars:
            block = block[:max_chars]
        return block

    async def write_turn(
        self,
        *,
        portal_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_response: str,
    ) -> None:
        wing = self._client.build_wing(portal_id, user_id, self._wing_strategy)
        room = self._client.build_room(portal_id, self._default_room)
        
        add_tool = self._client.resolve_tool_name("mempalace_add_drawer")
        if not add_tool:
            return

        content = f"[Session: {session_id}]\n[User]: {user_message}\n[Assistant]: {assistant_response}"
        await self._client.call(
            add_tool,
            {"wing": wing, "room": room, "content": content},
        )

    async def write_archive(
        self,
        *,
        portal_id: str,
        user_id: str,
        wing: str,
        room: str,
        content: str,
    ) -> Optional[str]:
        add_tool = self._client.resolve_tool_name("mempalace_add_drawer")
        if not add_tool:
            return None

        result = await self._client.call(
            add_tool,
            {"wing": wing, "room": room, "content": content},
        )
        if isinstance(result, dict) and result.get("id"):
            return result["id"]
        return None

    @staticmethod
    def _parse_search_response(
        resp: Any,
        *,
        portal_id: str,
        user_id: str,
    ) -> List[PortalMemoryEntry]:
        now = datetime.now()
        out: List[PortalMemoryEntry] = []
        if not isinstance(resp, dict):
            logger.error("MemPalace search response is not a dict: %s", type(resp))
            return out

        if isinstance(resp.get("drawers"), list):
            for item in resp["drawers"]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or item.get("text") or "").strip()
                if not content:
                    continue
                out.append(
                    PortalMemoryEntry(
                        id=str(item.get("id") or item.get("drawer_id") or f"mempalace_{len(out)}"),
                        portal_id=portal_id,
                        user_id=user_id,
                        content=content,
                        memory_type="context",
                        importance=float(item.get("importance") or 0.6),
                        confidence_score=float(item.get("confidence") or 0.7),
                        created_at=now,
                        last_accessed=now,
                        access_count=1,
                    )
                )
            return out

        if isinstance(resp.get("results"), list):
            logger.warning("MemPalace search response uses legacy key 'results': keys=%s", sorted(resp.keys()))
            for item in resp["results"]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or item.get("document") or item.get("text") or "").strip()
                if not content:
                    continue
                out.append(
                    PortalMemoryEntry(
                        id=str(item.get("id") or item.get("drawer_id") or f"mempalace_{len(out)}"),
                        portal_id=portal_id,
                        user_id=user_id,
                        content=content,
                        memory_type="context",
                        importance=0.6,
                        confidence_score=0.7,
                        created_at=now,
                        last_accessed=now,
                        access_count=1,
                    )
                )
            return out

        if isinstance(resp.get("documents"), list):
            logger.warning("MemPalace search response uses legacy key 'documents': keys=%s", sorted(resp.keys()))
            documents = resp.get("documents")
            ids = resp.get("ids") or []
            if documents and isinstance(documents[0], list):
                documents = documents[0]
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            for idx, doc in enumerate(documents or []):
                content = str(doc or "").strip()
                if not content:
                    continue
                entry_id = None
                if idx < len(ids):
                    entry_id = ids[idx]
                out.append(
                    PortalMemoryEntry(
                        id=str(entry_id or f"mempalace_{len(out)}"),
                        portal_id=portal_id,
                        user_id=user_id,
                        content=content,
                        memory_type="context",
                        importance=0.6,
                        confidence_score=0.7,
                        created_at=now,
                        last_accessed=now,
                        access_count=1,
                    )
                )
            return out

        logger.error(
            "MemPalace search response schema mismatch: keys=%s",
            sorted(resp.keys()),
        )
        return out
