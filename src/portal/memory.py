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
from typing import List, Optional
from uuid import uuid4

from ..core.models import PortalMemoryEntry

logger = logging.getLogger(__name__)

# Storage collection name
MEMORY_COLLECTION = "portal_memories"
GLOBAL_MEMORY_COLLECTION = "global_user_memories"


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
        confidence_score: Optional[float] = None,
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
            confidence_score=self._normalise_confidence(
                confidence_score if confidence_score is not None else self._derive_confidence(importance, memory_type)
            ),
            confidence_tier=self._confidence_tier(
                confidence_score if confidence_score is not None else self._derive_confidence(importance, memory_type)
            ),
            source_session_id=source_session_id,
            tags=tags or [],
        )
        existing = await self._load_all_entries(portal_id, user_id)
        conflict_ids, conflict_reason = self._detect_conflicts(entry, existing)
        if conflict_ids:
            entry.conflict_with = conflict_ids
            entry.conflict_reason = conflict_reason
        await self._save_entry(entry)
        if conflict_ids:
            await self._backfill_conflict_links(
                entry_id=entry.id,
                conflict_ids=conflict_ids,
                reason=conflict_reason or "Potential contradiction detected",
                collection=MEMORY_COLLECTION,
            )
        logger.debug(f"[Memory] Added {memory_type} for user={user_id}: {content[:80]}")
        return entry

    async def add_global(
        self,
        user_id: str,
        content: str,
        memory_type: str = "fact",
        importance: float = 0.5,
        confidence_score: Optional[float] = None,
        source_session_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> PortalMemoryEntry:
        """
        Add a new global user memory entry (cross-portal shared).
        """
        entry = PortalMemoryEntry(
            id=str(uuid4()),
            portal_id="__global__",
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            confidence_score=self._normalise_confidence(
                confidence_score if confidence_score is not None else self._derive_confidence(importance, memory_type)
            ),
            confidence_tier=self._confidence_tier(
                confidence_score if confidence_score is not None else self._derive_confidence(importance, memory_type)
            ),
            source_session_id=source_session_id,
            tags=tags or [],
        )
        existing = await self._load_all_global_entries(user_id)
        conflict_ids, conflict_reason = self._detect_conflicts(entry, existing)
        if conflict_ids:
            entry.conflict_with = conflict_ids
            entry.conflict_reason = conflict_reason
        await self._save_entry(entry, collection=GLOBAL_MEMORY_COLLECTION)
        if conflict_ids:
            await self._backfill_conflict_links(
                entry_id=entry.id,
                conflict_ids=conflict_ids,
                reason=conflict_reason or "Potential contradiction detected",
                collection=GLOBAL_MEMORY_COLLECTION,
            )
        logger.debug(f"[Memory] Added global {memory_type} for user={user_id}: {content[:80]}")
        return entry

    async def retrieve(
        self,
        portal_id: str,
        user_id: str,
        query: str = "",
        top_k: int = 10,
        memory_types: Optional[List[str]] = None,
        include_global: bool = False,
        min_confidence: float = 0.0,
        confidence_tier: Optional[str] = None,
        include_conflicted: bool = True,
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
        portal_entries = await self._load_all_entries(portal_id, user_id)
        global_entries = await self._load_all_global_entries(user_id) if include_global else []

        if memory_types:
            portal_entries = [e for e in portal_entries if e.memory_type in memory_types]
            global_entries = [e for e in global_entries if e.memory_type in memory_types]
        min_conf = self._normalise_confidence(min_confidence)
        if min_conf > 0.0:
            portal_entries = [e for e in portal_entries if self._normalise_confidence(e.confidence_score) >= min_conf]
            global_entries = [e for e in global_entries if self._normalise_confidence(e.confidence_score) >= min_conf]
        if confidence_tier:
            expected_tier = confidence_tier.strip().lower()
            portal_entries = [e for e in portal_entries if (e.confidence_tier or self._confidence_tier(e.confidence_score)) == expected_tier]
            global_entries = [e for e in global_entries if (e.confidence_tier or self._confidence_tier(e.confidence_score)) == expected_tier]
        if not include_conflicted:
            portal_entries = [e for e in portal_entries if not e.conflict_with]
            global_entries = [e for e in global_entries if not e.conflict_with]

        scoped_entries: List[tuple[str, PortalMemoryEntry]] = [
            ("portal", e) for e in portal_entries
        ] + [
            ("global", e) for e in global_entries
        ]
        if not scoped_entries:
            return []

        # Score each entry
        query_tokens = set(self._tokenize(query)) if query else set()
        now_ts = datetime.now().timestamp()

        scored: List[tuple[float, str, PortalMemoryEntry]] = []
        for scope, entry in scoped_entries:
            relevance = self._keyword_score(query_tokens, entry.content) if query_tokens else 1.0
            # Recency decay: entries from the last 24h get full score
            age_hours = (now_ts - entry.created_at.timestamp()) / 3600
            recency = max(0.1, 1.0 - age_hours / (24 * 30))  # decay over 30 days
            # Small boost for portal-local memories so local context wins on tie.
            scope_weight = 1.0 if scope == "portal" else 0.95
            confidence_weight = max(0.2, self._normalise_confidence(entry.confidence_score))
            conflict_penalty = 0.75 if entry.conflict_with else 1.0
            score = relevance * entry.importance * recency * scope_weight * confidence_weight * conflict_penalty
            scored.append((score, scope, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [(scope, e) for _, scope, e in scored[:top_k]]

        # Update access stats
        for scope, entry in top:
            entry.last_accessed = datetime.now()
            entry.access_count += 1
            await self._save_entry(
                entry,
                collection=MEMORY_COLLECTION if scope == "portal" else GLOBAL_MEMORY_COLLECTION,
            )

        return [entry for _, entry in top]

    async def retrieve_by_session(
        self,
        portal_id: str,
        user_id: str,
        session_id: str,
        top_k: int = 20,
        memory_types: Optional[List[str]] = None,
    ) -> List[PortalMemoryEntry]:
        """
        Retrieve memories created from a specific source session.

        Useful for "session retrieval" flows where we need to recall what was
        remembered from a previous conversation thread.
        """
        entries = await self._load_all_entries(portal_id, user_id)
        entries = [e for e in entries if e.source_session_id == session_id]
        if memory_types:
            entries = [e for e in entries if e.memory_type in memory_types]
        entries.sort(
            key=lambda e: (e.importance, e.last_accessed.timestamp(), e.access_count),
            reverse=True,
        )
        top = entries[:top_k]
        for entry in top:
            entry.last_accessed = datetime.now()
            entry.access_count += 1
            await self._save_entry(entry)
        return top

    async def bounded_snapshot(
        self,
        portal_id: str,
        user_id: str,
        max_chars: int = 1200,
        max_entries: int = 12,
        min_importance: float = 0.0,
        memory_types: Optional[List[str]] = None,
        include_global: bool = False,
        min_confidence: float = 0.0,
        include_conflicted: bool = True,
    ) -> str:
        """
        Build a bounded memory snapshot for prompt injection.

        - Fixed upper bounds by characters and entry count
        - Prioritises high-importance and recently-accessed memories
        - Returns stable text block suitable for frozen-prefix style injection
        """
        if max_chars <= 0 or max_entries <= 0:
            return ""

        portal_entries = await self._load_all_entries(portal_id, user_id)
        global_entries = await self._load_all_global_entries(user_id) if include_global else []
        entries = portal_entries + global_entries
        threshold = max(0.0, min(1.0, float(min_importance)))
        entries = [e for e in entries if e.importance >= threshold]
        min_conf = self._normalise_confidence(min_confidence)
        if min_conf > 0.0:
            entries = [e for e in entries if self._normalise_confidence(e.confidence_score) >= min_conf]
        if not include_conflicted:
            entries = [e for e in entries if not e.conflict_with]
        if memory_types:
            entries = [e for e in entries if e.memory_type in memory_types]
        if not entries:
            return ""

        entries.sort(
            key=lambda e: (e.importance, e.last_accessed.timestamp(), e.access_count),
            reverse=True,
        )

        lines: List[str] = []
        for entry in entries[: max(max_entries * 3, max_entries)]:
            tier = entry.confidence_tier or self._confidence_tier(entry.confidence_score)
            conflict_mark = " ⚠️冲突" if entry.conflict_with else ""
            line = f"- [{entry.memory_type}|{tier}] {entry.content.strip()}{conflict_mark}"
            if not line.strip():
                continue
            candidate = "\n".join(lines + [line]) if lines else line
            if len(candidate) > max_chars:
                break
            lines.append(line)
            if len(lines) >= max_entries:
                break
        return "\n".join(lines)

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

    async def clear_global(self, user_id: str) -> int:
        """Clear all global memories for a user. Returns deleted count."""
        entries = await self._load_all_global_entries(user_id)
        count = 0
        for entry in entries:
            try:
                ok = await self._storage.backend.delete(GLOBAL_MEMORY_COLLECTION, entry.id)
            except Exception:
                ok = False
            if ok:
                count += 1
        logger.info(f"[Memory] Cleared {count} global entries for user={user_id}")
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

        threshold = max(0.0, min(1.0, float(importance_threshold)))
        overflow = len(entries) - max_entries

        # Sort low-value candidates first (to be deleted first)
        def delete_key(e: PortalMemoryEntry):
            return (
                e.importance,
                e.last_accessed.timestamp(),
                e.access_count,
                e.created_at.timestamp(),
            )

        below_threshold = [e for e in entries if e.importance < threshold]
        above_threshold = [e for e in entries if e.importance >= threshold]
        below_threshold.sort(key=delete_key)
        above_threshold.sort(key=delete_key)

        # Respect threshold first, then fall back to higher-importance entries if required
        to_delete = below_threshold[:overflow]
        remaining = overflow - len(to_delete)
        if remaining > 0:
            to_delete.extend(above_threshold[:remaining])

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
        include_global: bool = False,
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
                    confidence_score=float(item.get("confidence_score", item.get("importance", 0.5))),
                    source_session_id=session_id,
                    tags=item.get("tags", []),
                )
                created.append(entry)
                if include_global:
                    await self.add_global(
                        user_id=user_id,
                        content=item.get("content", ""),
                        memory_type=item.get("memory_type", "fact"),
                        importance=float(item.get("importance", 0.5)),
                        confidence_score=float(item.get("confidence_score", item.get("importance", 0.5))),
                        source_session_id=session_id,
                        tags=item.get("tags", []),
                    )

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

    async def _save_entry(self, entry: PortalMemoryEntry, collection: str = MEMORY_COLLECTION) -> None:
        entry.confidence_score = self._normalise_confidence(entry.confidence_score)
        entry.confidence_tier = self._confidence_tier(entry.confidence_score)
        data = entry.model_dump()
        # Convert datetime to isoformat for JSON serialisation
        for field in ("created_at", "last_accessed"):
            if isinstance(data.get(field), datetime):
                data[field] = data[field].isoformat()
        await self._storage.backend.save(collection, entry.id, data)

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

    async def _load_all_global_entries(self, user_id: str) -> List[PortalMemoryEntry]:
        all_data = await self._storage.backend.list_all(GLOBAL_MEMORY_COLLECTION)
        entries: List[PortalMemoryEntry] = []
        for data in all_data:
            try:
                entry = PortalMemoryEntry(**data)
                if entry.user_id == user_id:
                    entries.append(entry)
            except Exception as e:
                logger.warning(f"[Memory] Skipping malformed global entry: {e}")
        return entries

    async def prune_global(
        self,
        user_id: str,
        max_entries: int,
        importance_threshold: float = 0.3,
    ) -> int:
        """Prune global memories to stay within max_entries for a user."""
        entries = await self._load_all_global_entries(user_id)
        if len(entries) <= max_entries:
            return 0

        threshold = max(0.0, min(1.0, float(importance_threshold)))
        overflow = len(entries) - max_entries

        def delete_key(e: PortalMemoryEntry):
            return (
                e.importance,
                e.last_accessed.timestamp(),
                e.access_count,
                e.created_at.timestamp(),
            )

        below_threshold = [e for e in entries if e.importance < threshold]
        above_threshold = [e for e in entries if e.importance >= threshold]
        below_threshold.sort(key=delete_key)
        above_threshold.sort(key=delete_key)

        to_delete = below_threshold[:overflow]
        remaining = overflow - len(to_delete)
        if remaining > 0:
            to_delete.extend(above_threshold[:remaining])

        deleted = 0
        for entry in to_delete:
            try:
                ok = await self._storage.backend.delete(GLOBAL_MEMORY_COLLECTION, entry.id)
            except Exception:
                ok = False
            if ok:
                deleted += 1

        logger.info(f"[Memory] Pruned {deleted} global entries for user={user_id}")
        return deleted

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokeniser for English words + Chinese characters."""
        lowered = text.lower()
        tokens: List[str] = []
        tokens.extend(re.findall(r"[a-z0-9]+", lowered))
        for chunk in re.findall(r"[\u4e00-\u9fff]+", lowered):
            tokens.extend(list(chunk))
        return tokens

    @staticmethod
    def _keyword_score(query_tokens: set, content: str) -> float:
        """Fraction of query tokens found in content."""
        if not query_tokens:
            return 1.0
        content_tokens = set(PortalMemoryManager._tokenize(content))
        overlap = query_tokens & content_tokens
        return len(overlap) / len(query_tokens)

    @staticmethod
    def _normalise_confidence(score: float) -> float:
        return max(0.0, min(1.0, float(score)))

    @staticmethod
    def _derive_confidence(importance: float, memory_type: str) -> float:
        base = max(0.0, min(1.0, float(importance)))
        type_bias = {
            "preference": 0.10,
            "fact": 0.0,
            "context": -0.05,
            "summary": -0.10,
        }.get((memory_type or "fact").lower(), 0.0)
        return max(0.0, min(1.0, base + type_bias))

    @staticmethod
    def _confidence_tier(score: float) -> str:
        s = PortalMemoryManager._normalise_confidence(score)
        if s >= 0.75:
            return "high"
        if s >= 0.45:
            return "medium"
        return "low"

    async def _backfill_conflict_links(
        self,
        entry_id: str,
        conflict_ids: List[str],
        reason: str,
        collection: str,
    ) -> None:
        for cid in conflict_ids:
            raw = await self._storage.backend.load(collection, cid)
            if not raw:
                continue
            try:
                existing = PortalMemoryEntry(**raw)
            except Exception:
                continue
            if entry_id not in existing.conflict_with:
                existing.conflict_with.append(entry_id)
            if not existing.conflict_reason:
                existing.conflict_reason = reason
            await self._save_entry(existing, collection=collection)

    def _detect_conflicts(
        self,
        new_entry: PortalMemoryEntry,
        existing_entries: List[PortalMemoryEntry],
    ) -> tuple[List[str], Optional[str]]:
        conflict_ids: List[str] = []
        reason: Optional[str] = None
        new_tokens = set(self._tokenize(new_entry.content))
        new_polarity = self._statement_polarity(new_entry.content)
        new_numbers = set(re.findall(r"\d+(?:\.\d+)?", new_entry.content))

        for item in existing_entries:
            if item.memory_type != new_entry.memory_type:
                continue
            old_tokens = set(self._tokenize(item.content))
            if not new_tokens or not old_tokens:
                continue
            overlap_ratio = len(new_tokens & old_tokens) / max(1, len(new_tokens | old_tokens))
            if overlap_ratio < 0.35:
                continue

            old_polarity = self._statement_polarity(item.content)
            old_numbers = set(re.findall(r"\d+(?:\.\d+)?", item.content))

            polarity_conflict = (new_polarity * old_polarity) < 0
            number_conflict = bool(new_numbers and old_numbers and new_numbers != old_numbers)
            if polarity_conflict or number_conflict:
                conflict_ids.append(item.id)
                reason = "Potential contradiction on same topic"
        return conflict_ids, reason

    @staticmethod
    def _statement_polarity(text: str) -> int:
        lowered = (text or "").lower()
        neg_markers = [
            "不", "没", "无", "非", "别", "不要", "不能", "不会",
            "not", "no", "never", "without", "can't", "cannot",
        ]
        return -1 if any(marker in lowered for marker in neg_markers) else 1
