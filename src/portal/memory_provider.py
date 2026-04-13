from __future__ import annotations

from typing import List, Optional, Protocol

from ..core.models import PortalMemoryEntry


class PortalMemoryProvider(Protocol):
    async def retrieve(
        self,
        *,
        portal_id: str,
        user_id: str,
        query: str,
        top_k: int,
        include_global: bool,
        session_id: Optional[str],
    ) -> List[PortalMemoryEntry]: ...

    async def bounded_snapshot(
        self,
        *,
        portal_id: str,
        user_id: str,
        max_chars: int,
        max_entries: int,
        include_global: bool,
    ) -> str: ...

    async def write_turn(
        self,
        *,
        portal_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_response: str,
    ) -> None: ...

