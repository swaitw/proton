"""
Portal Memory Manager — Platform-level long-term memory capability.

Stores and retrieves per-user memory entries for a given portal.
Memory is persisted in the storage backend (file / SQLite / Postgres)
and is available as a platform primitive that other features can also use.

Design goals:
- Importance-based pruning to stay within max_memory_entries
- Semantic-ish retrieval via simple keyword matching (no vector DB required)
- Every access updates last_accessed and access_count for LRU/LFU pruning
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ..core.models import PortalMemoryEntry

logger = logging.getLogger(__name__)

# Storage collection name
MEMORY_COLLECTION = "portal_memories"


class PortalMemoryManager:
    """
    Platform-level memory capability.

    Usage:
        memory = PortalMemoryManager(storage)
        await memory.add(portal_id, user_id, "User prefers Chinese.", memory_type="preference")
        entries = await memory.retrieve(portal_id, user_id, query="language preference", top_k=5)
    """

    def __init__(self, storage):
        self._storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add(
        self,
        portal_id: str,
        user_id: str,
        content: str,
        memory_type: str = "fact",
        importance: float = 0.5,
        source_session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> PortalMemoryEntry:
        """
        Add a new memory entry.

        Args:
            portal_id: Portal this memory belongs to
            user_id: User this memory belongs to
            content: The memory content
            memory_type: 'fact' | 'preference' | 'context' | 'summary'
            importance: 0.0–1.0
            source_session_id: Session that produced this memory
            tags: Optional tags for filtering

        Returns:
            The created PortalMemoryEntry
        """
        entry = PortalMemoryEntry(
            id=str(uuid4()),
            portal_id=portal_id,
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            source_session_id=source_session_id,
            tags=tags or [],
        )

        await self._save_entry(entry)
        logger.debug(f"[Memory] Added {memory_type} for user={user_id}: {content[:80]}")
        return entry

    async def retrieve(
        self,
        portal_id: str,
        user_id: str,
        query: str = "",
        top_k: int = 10,
        memory_types: Optional[List[str]] = None,
    ) -> List[PortalMemoryEntry]:
        """
        Retrieve relevant memories for a query.

        Uses keyword overlap scoring — no vector DB needed.
        Results are sorted by: relevance * importance * recency.

        Args:
            portal_id: Portal scope
            user_id: User scope
            query: Natural language query to match against
            top_k: Max entries to return
            memory_types: Optional filter by type

        Returns:
            List of PortalMemoryEntry sorted by relevance
        """
        all_entries = await self._load_all_entries(portal_id, user_id)

        if memory_types:
            all_entries = [e for e in all_entries if e.memory_type in memory_types]

        if not all_entries:
            return []

        # Score each entry
        query_tokens = set(self._tokenize(query)) if query else set()
        now_ts = datetime.now().timestamp()

        scored: List[tuple[float, PortalMemoryEntry]] = []
        for entry in all_entries:
            relevance = self._keyword_score(query_tokens, entry.content) if query_tokens else 1.0
            # Recency decay: entries from the last 24h get full score
            age_hours = (now_ts - entry.created_at.timestamp()) / 3600
            recency = max(0.1, 1.0 - age_hours / (24 * 30))  # decay over 30 days
            score = relevance * entry.importance * recency
            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for _, e in scored[:top_k]]

        # Update access stats
        for entry in top:
            entry.last_accessed = datetime.now()
            entry.access_count += 1
            await self._save_entry(entry)

        return top

    async def list_all(
        self,
        portal_id: str,
        user_id: str,
    ) -> List[PortalMemoryEntry]:
        """Return all memory entries for a portal + user."""
        return await self._load_all_entries(portal_id, user_id)

    async def delete(self, entry_id: str) -> bool:
        """Delete a memory entry by ID."""
        try:
            return await self._storage.backend.delete(MEMORY_COLLECTION, entry_id)
        except Exception as e:
            logger.error(f"[Memory] Delete failed: {e}")
            return False

    async def clear(self, portal_id: str, user_id: str) -> int:
        """Clear all memories for a user in a portal. Returns deleted count."""
        entries = await self._load_all_entries(portal_id, user_id)
        count = 0
        for entry in entries:
            if await self.delete(entry.id):
                count += 1
        logger.info(f"[Memory] Cleared {count} entries for portal={portal_id} user={user_id}")
        return count

    async def prune(
        self,
        portal_id: str,
        user_id: str,
        max_entries: int,
        importance_threshold: float = 0.3,
    ) -> int:
        """
        Prune memories to stay within max_entries.

        Strategy: remove least important + least recently accessed entries first,
        but always preserve entries above importance_threshold if possible.

        Returns number of entries deleted.
        """
        entries = await self._load_all_entries(portal_id, user_id)
        if len(entries) <= max_entries:
            return 0

        # Sort: highest importance + most recent = keep
        def sort_key(e: PortalMemoryEntry):
            age = (datetime.now() - e.last_accessed).total_seconds()
            return e.importance - (age / 86400 * 0.1)  # small recency penalty

        entries.sort(key=sort_key, reverse=True)
        to_delete = entries[max_entries:]

        deleted = 0
        for entry in to_delete:
            if await self.delete(entry.id):
                deleted += 1

        logger.info(f"[Memory] Pruned {deleted} entries for portal={portal_id} user={user_id}")
        return deleted

    # ------------------------------------------------------------------
    # LLM-assisted memory extraction (platform capability)
    # ------------------------------------------------------------------

    async def extract_and_store(
        self,
        portal_id: str,
        user_id: str,
        conversation_turn: str,
        session_id: str,
        llm_client,
        model: str = "gpt-4",
    ) -> List[PortalMemoryEntry]:
        """
        Use LLM to extract memorable facts/preferences from a conversation turn
        and store them automatically.

        This is a platform-level capability that can be reused by any feature.

        Args:
            portal_id: Portal scope
            user_id: User scope
            conversation_turn: The raw conversation text to extract from
            session_id: Source session ID
            llm_client: An AsyncOpenAI-compatible client
            model: Model to use for extraction

        Returns:
            List of newly created PortalMemoryEntry objects
        """
        prompt = f"""Analyse the following conversation excerpt and extract memorable facts, user preferences, or important context that should be remembered for future conversations.

Return a JSON array. Each item must have:
- "content": string — the memory in one concise sentence
- "memory_type": "fact" | "preference" | "context"
- "importance": float between 0.0 and 1.0
- "tags": array of short keyword strings

Only extract genuinely useful, non-trivial information. Return [] if nothing is worth remembering.
Return ONLY valid JSON, no markdown, no explanation.

Conversation:
{conversation_turn}
"""
        try:
            resp = await llm_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1024,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip possible markdown fences
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            items = json.loads(raw)

            created = []
            for item in items:
                entry = await self.add(
                    portal_id=portal_id,
                    user_id=user_id,
                    content=item.get("content", ""),
                    memory_type=item.get("memory_type", "fact"),
                    importance=float(item.get("importance", 0.5)),
                    source_session_id=session_id,
                    tags=item.get("tags", []),
                )
                created.append(entry)

            logger.info(f"[Memory] Extracted {len(created)} memories from conversation turn")
            return created

        except Exception as e:
            logger.error(f"[Memory] extract_and_store failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _storage_key(self, entry_id: str) -> str:
        return entry_id

    async def _save_entry(self, entry: PortalMemoryEntry) -> None:
        data = entry.model_dump()
        # Convert datetime to isoformat for JSON serialisation
        for field in ("created_at", "last_accessed"):
            if isinstance(data.get(field), datetime):
                data[field] = data[field].isoformat()
        await self._storage.backend.save(MEMORY_COLLECTION, entry.id, data)

    async def _load_all_entries(
        self,
        portal_id: str,
        user_id: str,
    ) -> List[PortalMemoryEntry]:
        all_data = await self._storage.backend.list_all(MEMORY_COLLECTION)
        entries = []
        for data in all_data:
            try:
                entry = PortalMemoryEntry(**data)
                if entry.portal_id == portal_id and entry.user_id == user_id:
                    entries.append(entry)
            except Exception as e:
                logger.warning(f"[Memory] Skipping malformed entry: {e}")
        return entries

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokeniser: lowercase words, remove punctuation."""
        return re.findall(r"[a-z\u4e00-\u9fff]+", text.lower())

    @staticmethod
    def _keyword_score(query_tokens: set, content: str) -> float:
        """Fraction of query tokens found in content."""
        if not query_tokens:
            return 1.0
        content_tokens = set(PortalMemoryManager._tokenize(content))
        overlap = query_tokens & content_tokens
        return len(overlap) / len(query_tokens)
