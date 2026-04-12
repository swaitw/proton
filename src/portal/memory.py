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
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ..core.models import PortalMemoryEntry

logger = logging.getLogger(__name__)

# Storage collection name
MEMORY_COLLECTION = "portal_memories"
GLOBAL_MEMORY_COLLECTION = "global_user_memories"
MEMORY_RETRIEVAL_TRACE_COLLECTION = "portal_memory_retrieval_traces"
NEAR_DUPLICATE_THRESHOLD = 0.82
CONFLICT_STATUS_NONE = "none"
CONFLICT_STATUS_PENDING = "pending"
CONFLICT_STATUS_CONFIRMED = "confirmed"
CONFLICT_STATUS_RESOLVED = "resolved"
DEFAULT_RETRIEVAL_STRATEGY = "balanced"
SUPPORTED_RETRIEVAL_STRATEGIES = {"balanced", "lexical_first", "semantic_first"}


class PortalMemoryManager:
    """
    Platform-level memory capability.

    Usage:
        memory = PortalMemoryManager(storage)
        await memory.add(portal_id, user_id, "User prefers Chinese.", memory_type="preference")
        entries = await memory.retrieve(portal_id, user_id, query="language preference", top_k=5)
    """

    def __init__(self, storage, ttl_policy: Optional[Dict[str, Any]] = None):
        self._storage = storage
        policy = ttl_policy or {}
        self._ttl_hot_hours = max(1, int(policy.get("hot_hours", 24 * 30)))
        self._ttl_warm_hours = max(1, int(policy.get("warm_hours", 24 * 14)))
        self._ttl_cold_hours = max(1, int(policy.get("cold_hours", 24 * 3)))
        self._ttl_hot_importance = max(0.0, min(1.0, float(policy.get("hot_importance", 0.8))))
        self._ttl_warm_importance = max(0.0, min(1.0, float(policy.get("warm_importance", 0.5))))

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
        self._ensure_source_index_seed(entry)
        self._refresh_ttl(entry)
        existing = await self._load_all_entries(portal_id, user_id)
        visible_existing = [e for e in existing if (not e.merged_into) and (not e.archived)]

        merged_into = await self._merge_into_near_duplicate(
            new_entry=entry,
            existing_entries=visible_existing,
            collection=MEMORY_COLLECTION,
        )
        if merged_into:
            logger.debug(f"[Memory] Near-duplicate merged into {merged_into.id} for user={user_id}")
            return merged_into

        conflict_ids, conflict_reason = self._detect_conflicts(entry, visible_existing)
        if conflict_ids:
            entry.conflict_with = conflict_ids
            entry.conflict_reason = conflict_reason
            entry.conflict_status = CONFLICT_STATUS_PENDING
            entry.requires_confirmation = True
            entry.conflict_updated_at = datetime.now()
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
        self._ensure_source_index_seed(entry)
        self._refresh_ttl(entry)
        existing = await self._load_all_global_entries(user_id)
        visible_existing = [e for e in existing if (not e.merged_into) and (not e.archived)]

        merged_into = await self._merge_into_near_duplicate(
            new_entry=entry,
            existing_entries=visible_existing,
            collection=GLOBAL_MEMORY_COLLECTION,
        )
        if merged_into:
            logger.debug(f"[Memory] Near-duplicate merged into global {merged_into.id} for user={user_id}")
            return merged_into

        conflict_ids, conflict_reason = self._detect_conflicts(entry, visible_existing)
        if conflict_ids:
            entry.conflict_with = conflict_ids
            entry.conflict_reason = conflict_reason
            entry.conflict_status = CONFLICT_STATUS_PENDING
            entry.requires_confirmation = True
            entry.conflict_updated_at = datetime.now()
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
        query_intent: Optional[str] = None,
        session_id: Optional[str] = None,
        retrieval_strategy: str = DEFAULT_RETRIEVAL_STRATEGY,
        strategy_decision: Optional[Dict[str, Any]] = None,
        request_source: str = "runtime",
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
        started_at = time.perf_counter()
        normalized_strategy = self._normalise_retrieval_strategy(retrieval_strategy)
        portal_entries = await self._load_all_entries(portal_id, user_id)
        await self._archive_expired_entries(portal_entries, collection=MEMORY_COLLECTION)
        if include_global:
            global_seed = await self._load_all_global_entries(user_id)
            await self._archive_expired_entries(global_seed, collection=GLOBAL_MEMORY_COLLECTION)
        portal_entries = await self._load_all_entries(portal_id, user_id)
        global_entries = await self._load_all_global_entries(user_id) if include_global else []
        portal_entries = [e for e in portal_entries if (not e.merged_into) and (not e.archived)]
        global_entries = [e for e in global_entries if (not e.merged_into) and (not e.archived)]

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
            portal_entries = [e for e in portal_entries if not self._is_active_conflict(e)]
            global_entries = [e for e in global_entries if not self._is_active_conflict(e)]

        scoped_entries: List[tuple[str, PortalMemoryEntry]] = [
            ("portal", e) for e in portal_entries
        ] + [
            ("global", e) for e in global_entries
        ]
        if not scoped_entries:
            await self._record_retrieval_trace(
                portal_id=portal_id,
                user_id=user_id,
                session_id=session_id,
                query=query,
                top_k=top_k,
                include_global=include_global,
                memory_types=memory_types,
                min_confidence=min_confidence,
                confidence_tier=confidence_tier,
                include_conflicted=include_conflicted,
                inferred_intent=(query_intent or self._infer_query_intent(query)).lower(),
                strategy=normalized_strategy,
                strategy_decision=strategy_decision,
                request_source=request_source,
                candidate_count=0,
                returned_count=0,
                top_matches=[],
                latency_ms=(time.perf_counter() - started_at) * 1000.0,
            )
            return []

        # Score each entry
        query_tokens = set(self._tokenize(query)) if query else set()
        inferred_intent = (query_intent or self._infer_query_intent(query)).lower()
        now_ts = datetime.now().timestamp()

        scored: List[tuple[float, str, PortalMemoryEntry]] = []
        for scope, entry in scoped_entries:
            if query_tokens:
                lexical = self._keyword_score(query_tokens, entry.content)
                semantic = self._semanticish_score(query, entry.content)
                relevance = self._relevance_by_strategy(
                    lexical=lexical,
                    semantic=semantic,
                    strategy=normalized_strategy,
                )
            else:
                relevance = 1.0
            # Recency decay: entries from the last 24h get full score
            age_hours = (now_ts - entry.created_at.timestamp()) / 3600
            recency = max(0.1, 1.0 - age_hours / (24 * 30))  # decay over 30 days
            # Small boost for portal-local memories so local context wins on tie.
            scope_weight = 1.0 if scope == "portal" else 0.95
            confidence_weight = max(0.2, self._normalise_confidence(entry.confidence_score))
            conflict_penalty = self._conflict_score_penalty(entry)
            intent_weight = self._intent_memory_type_weight(inferred_intent, entry.memory_type)
            score = relevance * entry.importance * recency * scope_weight * confidence_weight * conflict_penalty * intent_weight
            scored.append((score, scope, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [(scope, e) for _, scope, e in scored[:top_k]]

        # Update access stats
        for scope, entry in top:
            entry.last_accessed = datetime.now()
            entry.access_count += 1
            self._refresh_ttl(entry)
            await self._save_entry(
                entry,
                collection=MEMORY_COLLECTION if scope == "portal" else GLOBAL_MEMORY_COLLECTION,
            )

        top_matches = [
            {
                "entry_id": entry.id,
                "scope": scope,
                "memory_type": entry.memory_type,
                "score": round(score, 6),
                "importance": entry.importance,
                "confidence_tier": entry.confidence_tier,
                "conflict_status": entry.conflict_status,
            }
            for score, scope, entry in scored[:top_k]
        ]
        await self._record_retrieval_trace(
            portal_id=portal_id,
            user_id=user_id,
            session_id=session_id,
            query=query,
            top_k=top_k,
            include_global=include_global,
            memory_types=memory_types,
            min_confidence=min_confidence,
            confidence_tier=confidence_tier,
            include_conflicted=include_conflicted,
            inferred_intent=inferred_intent,
            strategy=normalized_strategy,
            strategy_decision=strategy_decision,
            request_source=request_source,
            candidate_count=len(scoped_entries),
            returned_count=len(top),
            top_matches=top_matches,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
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
        await self._archive_expired_entries(entries, collection=MEMORY_COLLECTION)
        entries = await self._load_all_entries(portal_id, user_id)
        entries = [
            e
            for e in entries
            if (not e.merged_into) and (not e.archived)
            and (
                e.source_session_id == session_id
                or any((idx.get("source_session_id") == session_id) for idx in (e.source_index or []))
            )
        ]
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
            self._refresh_ttl(entry)
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
        await self._archive_expired_entries(portal_entries, collection=MEMORY_COLLECTION)
        if include_global:
            global_seed = await self._load_all_global_entries(user_id)
            await self._archive_expired_entries(global_seed, collection=GLOBAL_MEMORY_COLLECTION)
        portal_entries = await self._load_all_entries(portal_id, user_id)
        global_entries = await self._load_all_global_entries(user_id) if include_global else []
        entries = [e for e in (portal_entries + global_entries) if (not e.merged_into) and (not e.archived)]
        threshold = max(0.0, min(1.0, float(min_importance)))
        entries = [e for e in entries if e.importance >= threshold]
        min_conf = self._normalise_confidence(min_confidence)
        if min_conf > 0.0:
            entries = [e for e in entries if self._normalise_confidence(e.confidence_score) >= min_conf]
        if not include_conflicted:
            entries = [e for e in entries if not self._is_active_conflict(e)]
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
            conflict_mark = " ⚠️冲突待确认" if self._is_active_conflict(entry) else ""
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
        entries = await self._load_all_entries(portal_id, user_id)
        await self._archive_expired_entries(entries, collection=MEMORY_COLLECTION)
        entries = await self._load_all_entries(portal_id, user_id)
        return [e for e in entries if (not e.merged_into) and (not e.archived)]

    async def list_pending_conflicts(
        self,
        portal_id: str,
        user_id: str,
        include_global: bool = False,
        top_k: int = 50,
    ) -> List[PortalMemoryEntry]:
        """Return conflict memories that are waiting for manual confirmation."""
        entries = await self._load_all_entries(portal_id, user_id)
        await self._archive_expired_entries(entries, collection=MEMORY_COLLECTION)
        if include_global:
            global_entries = await self._load_all_global_entries(user_id)
            await self._archive_expired_entries(global_entries, collection=GLOBAL_MEMORY_COLLECTION)
            entries.extend(await self._load_all_global_entries(user_id))
        entries = await self._load_all_entries(portal_id, user_id) + (await self._load_all_global_entries(user_id) if include_global else [])
        visible = [e for e in entries if (not e.merged_into) and (not e.archived) and self._is_pending_conflict(e)]
        visible.sort(
            key=lambda e: (e.importance, e.created_at.timestamp()),
            reverse=True,
        )
        if top_k <= 0:
            return []
        return visible[:int(top_k)]

    async def list_archived(
        self,
        portal_id: str,
        user_id: str,
        query: str = "",
        top_k: int = 20,
        include_global: bool = False,
        memory_types: Optional[List[str]] = None,
    ) -> List[PortalMemoryEntry]:
        """List archived memories (cold memory archive)."""
        portal_entries = await self._load_all_entries(portal_id, user_id)
        global_entries = await self._load_all_global_entries(user_id) if include_global else []
        entries = [e for e in (portal_entries + global_entries) if e.archived]
        if memory_types:
            entries = [e for e in entries if e.memory_type in memory_types]
        if not entries:
            return []

        if query.strip():
            query_tokens = set(self._tokenize(query))
            scored = []
            for entry in entries:
                lexical = self._keyword_score(query_tokens, entry.content)
                semantic = self._semanticish_score(query, entry.content)
                score = lexical * 0.6 + semantic * 0.4
                scored.append((score, entry))
            scored.sort(key=lambda x: (x[0], (x[1].archived_at or x[1].created_at).timestamp()), reverse=True)
            ranked = [e for _, e in scored]
        else:
            ranked = sorted(
                entries,
                key=lambda e: (e.archived_at or e.created_at).timestamp(),
                reverse=True,
            )
        return ranked[: max(0, int(top_k))]

    async def restore_archived(
        self,
        portal_id: str,
        user_id: str,
        entry_id: str,
        include_global: bool = False,
    ) -> Dict[str, Any]:
        """Restore one archived memory back to active state."""
        collection = MEMORY_COLLECTION
        raw = await self._storage.backend.load(collection, entry_id)
        if not raw and include_global:
            collection = GLOBAL_MEMORY_COLLECTION
            raw = await self._storage.backend.load(collection, entry_id)
        if not raw:
            return {"updated": False, "reason": "entry_not_found", "entry_id": entry_id}
        try:
            entry = PortalMemoryEntry(**raw)
        except Exception:
            return {"updated": False, "reason": "entry_malformed", "entry_id": entry_id}
        if entry.user_id != user_id:
            return {"updated": False, "reason": "entry_scope_mismatch", "entry_id": entry_id}
        if collection == MEMORY_COLLECTION and entry.portal_id != portal_id:
            return {"updated": False, "reason": "entry_scope_mismatch", "entry_id": entry_id}
        if not entry.archived:
            return {"updated": False, "reason": "entry_not_archived", "entry_id": entry_id}

        entry.archived = False
        entry.archived_at = None
        entry.archive_reason = None
        entry.restore_count += 1
        self._refresh_ttl(entry)
        await self._save_entry(entry, collection=collection)
        return {
            "updated": True,
            "entry_id": entry.id,
            "archived": entry.archived,
            "restore_count": entry.restore_count,
            "ttl_tier": entry.ttl_tier,
            "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
        }

    async def get_retrieval_observability_dashboard(
        self,
        portal_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        hours: int = 24,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """Aggregate memory retrieval traces for observability dashboard."""
        all_rows = await self._storage.backend.list_all(MEMORY_RETRIEVAL_TRACE_COLLECTION)
        normalized_hours = max(1, int(hours))
        normalized_limit = max(1, int(limit))
        cutoff = datetime.now().timestamp() - (normalized_hours * 3600)

        traces: List[Dict[str, Any]] = []
        for row in all_rows:
            if row.get("portal_id") != portal_id:
                continue
            if user_id and row.get("user_id") != user_id:
                continue
            if session_id and row.get("session_id") != session_id:
                continue
            ts = self._to_timestamp(row.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
            traces.append(row)

        traces.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        traces = traces[:normalized_limit]

        latencies = [self._safe_float(item.get("latency_ms")) for item in traces]
        latencies = [x for x in latencies if x is not None]
        candidate_sizes = [self._safe_float(item.get("candidate_count")) for item in traces]
        candidate_sizes = [x for x in candidate_sizes if x is not None]
        returned_sizes = [self._safe_float(item.get("returned_count")) for item in traces]
        returned_sizes = [x for x in returned_sizes if x is not None]

        strategy_distribution: Dict[str, int] = {}
        source_distribution: Dict[str, int] = {}
        users = set()
        sessions = set()
        for item in traces:
            strategy = str(item.get("strategy") or DEFAULT_RETRIEVAL_STRATEGY)
            source = str(item.get("strategy_source") or "default")
            strategy_distribution[strategy] = strategy_distribution.get(strategy, 0) + 1
            source_distribution[source] = source_distribution.get(source, 0) + 1
            if item.get("user_id"):
                users.add(str(item["user_id"]))
            if item.get("session_id"):
                sessions.add(str(item["session_id"]))

        return {
            "filters": {
                "portal_id": portal_id,
                "user_id": user_id,
                "session_id": session_id,
                "hours": normalized_hours,
                "limit": normalized_limit,
            },
            "metrics": {
                "total_queries": len(traces),
                "unique_users": len(users),
                "unique_sessions": len(sessions),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
                "p95_latency_ms": round(self._percentile(latencies, 95), 3) if latencies else 0.0,
                "avg_candidate_count": round(sum(candidate_sizes) / len(candidate_sizes), 3) if candidate_sizes else 0.0,
                "avg_returned_count": round(sum(returned_sizes) / len(returned_sizes), 3) if returned_sizes else 0.0,
                "strategy_distribution": strategy_distribution,
                "strategy_source_distribution": source_distribution,
            },
            "traces": traces[: min(50, len(traces))],
        }

    async def confirm_conflict(
        self,
        portal_id: str,
        user_id: str,
        entry_id: str,
        note: Optional[str] = None,
        include_global: bool = False,
    ) -> Dict[str, Any]:
        """
        Confirm one conflict memory as trusted.
        Conflicting pending memories are marked as resolved by this confirmation.
        """
        collection = MEMORY_COLLECTION
        raw = await self._storage.backend.load(collection, entry_id)
        if not raw and include_global:
            collection = GLOBAL_MEMORY_COLLECTION
            raw = await self._storage.backend.load(collection, entry_id)
        if not raw:
            return {"updated": False, "reason": "entry_not_found", "entry_id": entry_id}
        entry = PortalMemoryEntry(**raw)
        if entry.user_id != user_id or (collection == MEMORY_COLLECTION and entry.portal_id != portal_id):
            return {"updated": False, "reason": "entry_scope_mismatch", "entry_id": entry_id}
        if not entry.conflict_with:
            return {"updated": False, "reason": "entry_has_no_conflict", "entry_id": entry_id}

        entry.conflict_status = CONFLICT_STATUS_CONFIRMED
        entry.requires_confirmation = False
        entry.conflict_note = note or "manually_confirmed"
        entry.conflict_updated_at = datetime.now()
        await self._save_entry(entry, collection=collection)

        resolved_ids: List[str] = []
        for cid in entry.conflict_with:
            other_raw = await self._storage.backend.load(collection, cid)
            if not other_raw:
                continue
            try:
                other = PortalMemoryEntry(**other_raw)
            except Exception:
                continue
            if other.user_id != user_id:
                continue
            if collection == MEMORY_COLLECTION and other.portal_id != portal_id:
                continue
            if other.conflict_status == CONFLICT_STATUS_CONFIRMED:
                continue
            other.conflict_status = CONFLICT_STATUS_RESOLVED
            other.requires_confirmation = False
            other.conflict_note = f"superseded_by:{entry.id}"
            other.conflict_updated_at = datetime.now()
            await self._save_entry(other, collection=collection)
            resolved_ids.append(other.id)

        return {
            "updated": True,
            "entry_id": entry.id,
            "confirmed": True,
            "resolved_conflict_ids": resolved_ids,
            "conflict_status": entry.conflict_status,
        }

    async def resolve_conflict(
        self,
        portal_id: str,
        user_id: str,
        entry_id: str,
        note: Optional[str] = None,
        clear_links: bool = True,
        include_global: bool = False,
    ) -> Dict[str, Any]:
        """Resolve conflict state for an entry and optionally clear bidirectional links."""
        collection = MEMORY_COLLECTION
        raw = await self._storage.backend.load(collection, entry_id)
        if not raw and include_global:
            collection = GLOBAL_MEMORY_COLLECTION
            raw = await self._storage.backend.load(collection, entry_id)
        if not raw:
            return {"updated": False, "reason": "entry_not_found", "entry_id": entry_id}
        entry = PortalMemoryEntry(**raw)
        if entry.user_id != user_id or (collection == MEMORY_COLLECTION and entry.portal_id != portal_id):
            return {"updated": False, "reason": "entry_scope_mismatch", "entry_id": entry_id}

        linked_ids = list(entry.conflict_with or [])
        entry.conflict_status = CONFLICT_STATUS_RESOLVED
        entry.requires_confirmation = False
        entry.conflict_note = note or "manually_resolved"
        entry.conflict_updated_at = datetime.now()
        if clear_links:
            entry.conflict_with = []
            entry.conflict_reason = None
        await self._save_entry(entry, collection=collection)

        unlinked_from: List[str] = []
        if clear_links:
            for cid in linked_ids:
                other_raw = await self._storage.backend.load(collection, cid)
                if not other_raw:
                    continue
                try:
                    other = PortalMemoryEntry(**other_raw)
                except Exception:
                    continue
                if entry.id in (other.conflict_with or []):
                    other.conflict_with = [eid for eid in (other.conflict_with or []) if eid != entry.id]
                    if not other.conflict_with and other.conflict_status == CONFLICT_STATUS_PENDING:
                        other.conflict_status = CONFLICT_STATUS_NONE
                        other.requires_confirmation = False
                        other.conflict_reason = None
                    other.conflict_updated_at = datetime.now()
                    await self._save_entry(other, collection=collection)
                    unlinked_from.append(other.id)

        return {
            "updated": True,
            "entry_id": entry.id,
            "conflict_status": entry.conflict_status,
            "unlinked_from": unlinked_from,
        }

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

    async def unmerge(
        self,
        portal_id: str,
        user_id: str,
        entry_id: str,
        source_entry_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Reverse near-duplicate merge on a canonical memory.

        - source_entry_id=None: detach all merged children
        - source_entry_id=...: detach one merged child only
        """
        raw = await self._storage.backend.load(MEMORY_COLLECTION, entry_id)
        if not raw:
            return {"updated": False, "reason": "entry_not_found", "detached_ids": []}
        try:
            canonical = PortalMemoryEntry(**raw)
        except Exception:
            return {"updated": False, "reason": "entry_malformed", "detached_ids": []}
        if canonical.portal_id != portal_id or canonical.user_id != user_id:
            return {"updated": False, "reason": "entry_scope_mismatch", "detached_ids": []}
        if canonical.merged_into:
            return {"updated": False, "reason": "entry_is_not_canonical", "detached_ids": []}
        if not canonical.merged_from:
            return {"updated": False, "reason": "nothing_to_unmerge", "detached_ids": []}

        target_ids = [source_entry_id] if source_entry_id else list(canonical.merged_from)
        detached_ids: List[str] = []
        for cid in target_ids:
            if cid not in canonical.merged_from:
                continue
            child_raw = await self._storage.backend.load(MEMORY_COLLECTION, cid)
            if not child_raw:
                continue
            try:
                child = PortalMemoryEntry(**child_raw)
            except Exception:
                continue
            if child.merged_into != canonical.id:
                continue
            child.merged_into = None
            await self._save_entry(child)
            detached_ids.append(child.id)

        if not detached_ids:
            return {"updated": False, "reason": "no_child_detached", "detached_ids": []}

        canonical.merged_from = [mid for mid in canonical.merged_from if mid not in detached_ids]
        canonical.source_index = [
            idx for idx in (canonical.source_index or [])
            if (idx.get("source_entry_id") not in detached_ids)
        ]
        if not canonical.source_index:
            canonical.source_index = [self._build_source_index_item(canonical)]
        await self._save_entry(canonical)
        return {
            "updated": True,
            "detached_ids": detached_ids,
            "remaining_merged_from": canonical.merged_from,
        }

    async def merge_near_duplicates(
        self,
        portal_id: str,
        user_id: str,
        similarity_threshold: float = NEAR_DUPLICATE_THRESHOLD,
    ) -> Dict[str, Any]:
        """
        Batch merge near-duplicate memories and keep reversible source lineage.
        """
        threshold = max(0.0, min(1.0, float(similarity_threshold)))
        raw_entries = await self._load_all_entries(portal_id, user_id)
        visible = [e for e in raw_entries if (not e.merged_into) and (not e.archived)]
        visible.sort(key=lambda e: e.created_at.timestamp())

        merged_pairs: List[Dict[str, str]] = []
        for i, base in enumerate(visible):
            if base.merged_into:
                continue
            for candidate in visible[i + 1 :]:
                if candidate.merged_into:
                    continue
                if candidate.memory_type != base.memory_type:
                    continue
                if self._has_explicit_conflict(base.content, candidate.content):
                    continue
                score = self._near_duplicate_score(base.content, candidate.content)
                if score < threshold:
                    continue
                candidate.merged_into = base.id
                await self._save_entry(candidate)
                if candidate.id not in base.merged_from:
                    base.merged_from.append(candidate.id)
                base.source_index = self._merge_source_index(
                    base.source_index,
                    [self._build_source_index_item(candidate)],
                )
                base.content = self._merge_content(base.content, candidate.content)
                base.importance = max(base.importance, candidate.importance)
                base.confidence_score = max(
                    self._normalise_confidence(base.confidence_score),
                    self._normalise_confidence(candidate.confidence_score),
                )
                if candidate.tags:
                    base.tags = sorted(set((base.tags or []) + (candidate.tags or [])))
                await self._save_entry(base)
                merged_pairs.append({"canonical_id": base.id, "source_entry_id": candidate.id})

        return {"merged_count": len(merged_pairs), "merged_pairs": merged_pairs}

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

        Returns number of entries archived.
        """
        entries = await self._load_all_entries(portal_id, user_id)
        await self._archive_expired_entries(entries, collection=MEMORY_COLLECTION)
        entries = await self._load_all_entries(portal_id, user_id)
        entries = [e for e in entries if (not e.merged_into) and (not e.archived)]
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
        to_archive = below_threshold[:overflow]
        remaining = overflow - len(to_archive)
        if remaining > 0:
            to_archive.extend(above_threshold[:remaining])

        archived = 0
        for entry in to_archive:
            if await self._archive_entry(entry, collection=MEMORY_COLLECTION, reason="capacity_prune"):
                archived += 1

        logger.info(f"[Memory] Pruned (archived) {archived} entries for portal={portal_id} user={user_id}")
        return archived

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

    def _ttl_tier_for_importance(self, importance: float) -> str:
        value = max(0.0, min(1.0, float(importance)))
        if value >= self._ttl_hot_importance:
            return "hot"
        if value >= self._ttl_warm_importance:
            return "warm"
        return "cold"

    def _ttl_hours_by_tier(self, tier: str) -> int:
        t = (tier or "warm").strip().lower()
        if t == "hot":
            return self._ttl_hot_hours
        if t == "cold":
            return self._ttl_cold_hours
        return self._ttl_warm_hours

    def _refresh_ttl(self, entry: PortalMemoryEntry) -> None:
        if entry.archived:
            return
        tier = self._ttl_tier_for_importance(entry.importance)
        entry.ttl_tier = tier
        entry.expires_at = datetime.now() + timedelta(hours=self._ttl_hours_by_tier(tier))

    def _ensure_ttl(self, entry: PortalMemoryEntry) -> None:
        if entry.archived:
            return
        expected_tier = self._ttl_tier_for_importance(entry.importance)
        if not entry.ttl_tier:
            entry.ttl_tier = expected_tier
        if not entry.expires_at:
            entry.expires_at = datetime.now() + timedelta(hours=self._ttl_hours_by_tier(entry.ttl_tier))

    async def _archive_entry(self, entry: PortalMemoryEntry, collection: str, reason: str) -> bool:
        if entry.archived:
            return False
        entry.archived = True
        entry.archived_at = datetime.now()
        entry.archive_reason = reason
        await self._save_entry(entry, collection=collection)
        return True

    async def _archive_expired_entries(self, entries: List[PortalMemoryEntry], collection: str) -> int:
        now = datetime.now()
        archived_count = 0
        for entry in entries:
            if entry.archived:
                continue
            self._ensure_ttl(entry)
            if entry.expires_at and entry.expires_at <= now:
                if await self._archive_entry(
                    entry,
                    collection=collection,
                    reason=f"ttl_expired:{entry.ttl_tier or 'warm'}",
                ):
                    archived_count += 1
        return archived_count

    def _storage_key(self, entry_id: str) -> str:
        return entry_id

    async def _save_entry(self, entry: PortalMemoryEntry, collection: str = MEMORY_COLLECTION) -> None:
        entry.confidence_score = self._normalise_confidence(entry.confidence_score)
        entry.confidence_tier = self._confidence_tier(entry.confidence_score)
        self._sync_conflict_state(entry)
        self._ensure_source_index_seed(entry)
        self._ensure_ttl(entry)
        data = entry.model_dump()
        # Convert datetime to isoformat for JSON serialisation
        for field in ("created_at", "last_accessed", "conflict_updated_at", "expires_at", "archived_at"):
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
        await self._archive_expired_entries(entries, collection=GLOBAL_MEMORY_COLLECTION)
        entries = await self._load_all_global_entries(user_id)
        entries = [e for e in entries if (not e.merged_into) and (not e.archived)]
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

        to_archive = below_threshold[:overflow]
        remaining = overflow - len(to_archive)
        if remaining > 0:
            to_archive.extend(above_threshold[:remaining])

        archived = 0
        for entry in to_archive:
            if await self._archive_entry(entry, collection=GLOBAL_MEMORY_COLLECTION, reason="capacity_prune"):
                archived += 1

        logger.info(f"[Memory] Pruned (archived) {archived} global entries for user={user_id}")
        return archived

    async def _merge_into_near_duplicate(
        self,
        new_entry: PortalMemoryEntry,
        existing_entries: List[PortalMemoryEntry],
        collection: str,
    ) -> Optional[PortalMemoryEntry]:
        best: Optional[PortalMemoryEntry] = None
        best_score = 0.0
        for candidate in existing_entries:
            if candidate.memory_type != new_entry.memory_type:
                continue
            if candidate.id == new_entry.id:
                continue
            if candidate.conflict_with or new_entry.conflict_with:
                continue
            if self._has_explicit_conflict(candidate.content, new_entry.content):
                continue
            score = self._near_duplicate_score(candidate.content, new_entry.content)
            if score >= NEAR_DUPLICATE_THRESHOLD and score > best_score:
                best = candidate
                best_score = score
        if not best:
            return None

        new_entry.merged_into = best.id
        await self._save_entry(new_entry, collection=collection)

        if new_entry.id not in best.merged_from:
            best.merged_from.append(new_entry.id)
        best.source_index = self._merge_source_index(
            best.source_index,
            [self._build_source_index_item(new_entry)],
        )
        best.content = self._merge_content(best.content, new_entry.content)
        best.importance = max(best.importance, new_entry.importance)
        best.confidence_score = max(
            self._normalise_confidence(best.confidence_score),
            self._normalise_confidence(new_entry.confidence_score),
        )
        if not best.source_session_id and new_entry.source_session_id:
            best.source_session_id = new_entry.source_session_id
        if new_entry.tags:
            best.tags = sorted(set((best.tags or []) + (new_entry.tags or [])))
        await self._save_entry(best, collection=collection)
        return best

    @staticmethod
    def _near_duplicate_score(left: str, right: str) -> float:
        lexical = PortalMemoryManager._keyword_score(
            set(PortalMemoryManager._tokenize(left)),
            right,
        )
        semantic = PortalMemoryManager._semanticish_score(left, right)
        return (lexical * 0.45) + (semantic * 0.55)

    @staticmethod
    def _has_explicit_conflict(left: str, right: str) -> bool:
        p_left = PortalMemoryManager._statement_polarity(left)
        p_right = PortalMemoryManager._statement_polarity(right)
        if (p_left * p_right) < 0:
            return True
        n_left = set(re.findall(r"\d+(?:\.\d+)?", left or ""))
        n_right = set(re.findall(r"\d+(?:\.\d+)?", right or ""))
        return bool(n_left and n_right and n_left != n_right)

    @staticmethod
    def _merge_content(base: str, incoming: str) -> str:
        b = (base or "").strip()
        i = (incoming or "").strip()
        if not b:
            return i
        if not i:
            return b
        if i in b:
            return b
        if b in i:
            return i
        return f"{b}；{i}"

    @staticmethod
    def _build_source_index_item(entry: PortalMemoryEntry) -> Dict[str, Any]:
        return {
            "source_entry_id": entry.id,
            "source_session_id": entry.source_session_id,
            "content": entry.content,
            "created_at": entry.created_at.isoformat(),
            "memory_type": entry.memory_type,
        }

    def _ensure_source_index_seed(self, entry: PortalMemoryEntry) -> None:
        if entry.source_index:
            return
        entry.source_index = [self._build_source_index_item(entry)]

    @staticmethod
    def _merge_source_index(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = list(existing or [])
        existing_ids = {
            item.get("source_entry_id")
            for item in merged
            if isinstance(item, dict)
        }
        for item in incoming or []:
            sid = item.get("source_entry_id") if isinstance(item, dict) else None
            if not sid or sid in existing_ids:
                continue
            merged.append(item)
            existing_ids.add(sid)
        return merged

    @staticmethod
    def _normalise_conflict_status(status: Optional[str]) -> str:
        s = (status or "").strip().lower()
        if s in {CONFLICT_STATUS_PENDING, CONFLICT_STATUS_CONFIRMED, CONFLICT_STATUS_RESOLVED}:
            return s
        return CONFLICT_STATUS_NONE

    def _sync_conflict_state(self, entry: PortalMemoryEntry) -> None:
        entry.conflict_status = self._normalise_conflict_status(entry.conflict_status)
        if entry.conflict_status == CONFLICT_STATUS_PENDING and entry.conflict_with:
            entry.requires_confirmation = True
            if not entry.conflict_updated_at:
                entry.conflict_updated_at = datetime.now()
            return
        if not entry.conflict_with:
            if entry.conflict_status in {CONFLICT_STATUS_NONE, CONFLICT_STATUS_PENDING}:
                entry.conflict_status = CONFLICT_STATUS_NONE
            entry.requires_confirmation = False

    def _is_active_conflict(self, entry: PortalMemoryEntry) -> bool:
        if not entry.conflict_with:
            return False
        status = self._normalise_conflict_status(entry.conflict_status)
        return status in {CONFLICT_STATUS_PENDING, CONFLICT_STATUS_CONFIRMED}

    def _is_pending_conflict(self, entry: PortalMemoryEntry) -> bool:
        if not entry.conflict_with:
            return False
        return self._normalise_conflict_status(entry.conflict_status) == CONFLICT_STATUS_PENDING

    def _conflict_score_penalty(self, entry: PortalMemoryEntry) -> float:
        if not entry.conflict_with:
            return 1.0
        status = self._normalise_conflict_status(entry.conflict_status)
        if status == CONFLICT_STATUS_PENDING:
            return 0.50
        if status == CONFLICT_STATUS_CONFIRMED:
            return 0.85
        if status == CONFLICT_STATUS_RESOLVED:
            return 1.0
        return 0.75

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
    def _infer_query_intent(query: str) -> str:
        lowered = (query or "").lower()
        if not lowered.strip():
            return "general"
        preference_markers = [
            "偏好", "喜欢", "习惯", "风格", "常用",
            "prefer", "preference", "like", "habit", "usually",
        ]
        task_markers = [
            "继续", "下一步", "执行", "安排", "推进", "做完",
            "continue", "next", "execute", "run", "plan", "follow up",
        ]
        summary_markers = [
            "总结", "概括", "回顾", "摘要",
            "summary", "recap", "overview",
        ]
        if any(marker in lowered for marker in preference_markers):
            return "preference_lookup"
        if any(marker in lowered for marker in task_markers):
            return "task_continuation"
        if any(marker in lowered for marker in summary_markers):
            return "summary_lookup"
        return "fact_lookup"

    @staticmethod
    def _intent_memory_type_weight(intent: str, memory_type: str) -> float:
        weight_table = {
            "fact_lookup": {
                "fact": 1.18,
                "preference": 0.92,
                "context": 0.95,
                "summary": 0.90,
            },
            "preference_lookup": {
                "fact": 0.94,
                "preference": 1.22,
                "context": 0.90,
                "summary": 0.92,
            },
            "task_continuation": {
                "fact": 0.95,
                "preference": 0.88,
                "context": 1.20,
                "summary": 1.08,
            },
            "summary_lookup": {
                "fact": 0.92,
                "preference": 0.88,
                "context": 1.00,
                "summary": 1.25,
            },
            "general": {
                "fact": 1.0,
                "preference": 1.0,
                "context": 1.0,
                "summary": 1.0,
            },
        }
        intent_map = weight_table.get(intent, weight_table["general"])
        return float(intent_map.get((memory_type or "fact").lower(), 1.0))

    @staticmethod
    def _semanticish_score(query: str, content: str) -> float:
        query_tokens = set(PortalMemoryManager._expand_tokens_with_synonyms(PortalMemoryManager._tokenize(query)))
        content_tokens = set(PortalMemoryManager._expand_tokens_with_synonyms(PortalMemoryManager._tokenize(content)))
        if not query_tokens or not content_tokens:
            return 0.0
        token_jaccard = len(query_tokens & content_tokens) / len(query_tokens | content_tokens)
        query_ngrams = PortalMemoryManager._char_ngrams(query, n=2)
        content_ngrams = PortalMemoryManager._char_ngrams(content, n=2)
        if query_ngrams and content_ngrams:
            ngram_jaccard = len(query_ngrams & content_ngrams) / len(query_ngrams | content_ngrams)
        else:
            ngram_jaccard = 0.0
        return (token_jaccard * 0.7) + (ngram_jaccard * 0.3)

    @staticmethod
    def _char_ngrams(text: str, n: int = 2) -> set:
        cleaned = re.sub(r"\s+", "", (text or "").lower())
        if len(cleaned) < n:
            return set([cleaned]) if cleaned else set()
        return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}

    @staticmethod
    def _expand_tokens_with_synonyms(tokens: List[str]) -> List[str]:
        synonyms = {
            "报销": [" reimbursement", "refund", "费用报销", "差旅报销"],
            "差旅": ["出差", "travel", "trip"],
            "费用": ["开销", "cost", "expense"],
            "reimbursement": ["refund", "expense", "报销"],
            "refund": ["reimbursement", "报销"],
            "meeting": ["会议"],
            "会议": ["meeting"],
        }
        expanded = list(tokens)
        for token in tokens:
            for alias in synonyms.get(token, []):
                alias_token = alias.strip().lower()
                if alias_token:
                    expanded.append(alias_token)
        return expanded

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

    @staticmethod
    def _normalise_retrieval_strategy(strategy: Optional[str]) -> str:
        candidate = str(strategy or DEFAULT_RETRIEVAL_STRATEGY).strip().lower()
        if candidate in SUPPORTED_RETRIEVAL_STRATEGIES:
            return candidate
        return DEFAULT_RETRIEVAL_STRATEGY

    @staticmethod
    def _relevance_by_strategy(lexical: float, semantic: float, strategy: str) -> float:
        chosen = PortalMemoryManager._normalise_retrieval_strategy(strategy)
        if chosen == "lexical_first":
            return (lexical * 0.82) + (semantic * 0.18)
        if chosen == "semantic_first":
            return (lexical * 0.25) + (semantic * 0.75)
        return (lexical * 0.65) + (semantic * 0.35)

    async def _record_retrieval_trace(
        self,
        *,
        portal_id: str,
        user_id: str,
        session_id: Optional[str],
        query: str,
        top_k: int,
        include_global: bool,
        memory_types: Optional[List[str]],
        min_confidence: float,
        confidence_tier: Optional[str],
        include_conflicted: bool,
        inferred_intent: str,
        strategy: str,
        strategy_decision: Optional[Dict[str, Any]],
        request_source: str,
        candidate_count: int,
        returned_count: int,
        top_matches: List[Dict[str, Any]],
        latency_ms: float,
    ) -> None:
        trace_id = str(uuid4())
        decision = strategy_decision or {}
        payload = {
            "id": trace_id,
            "portal_id": portal_id,
            "user_id": user_id,
            "session_id": session_id,
            "query": query,
            "top_k": int(top_k),
            "include_global": bool(include_global),
            "memory_types": list(memory_types or []),
            "min_confidence": self._normalise_confidence(min_confidence),
            "confidence_tier": confidence_tier,
            "include_conflicted": bool(include_conflicted),
            "query_intent": inferred_intent,
            "strategy": self._normalise_retrieval_strategy(strategy),
            "strategy_source": str(decision.get("source") or "default"),
            "strategy_rule_id": decision.get("rule_id"),
            "strategy_rule_note": decision.get("note"),
            "strategy_policy_version": int(decision.get("version", 1)),
            "request_source": request_source,
            "candidate_count": max(0, int(candidate_count)),
            "returned_count": max(0, int(returned_count)),
            "latency_ms": round(max(0.0, float(latency_ms)), 3),
            "top_matches": top_matches,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            await self._storage.backend.save(MEMORY_RETRIEVAL_TRACE_COLLECTION, trace_id, payload)
        except Exception as e:
            logger.warning(f"[Memory] Failed to save retrieval trace: {e}")

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _to_timestamp(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, datetime):
            return value.timestamp()
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            return None

    @staticmethod
    def _percentile(samples: List[float], percentile: int) -> float:
        if not samples:
            return 0.0
        ordered = sorted(samples)
        if percentile <= 0:
            return float(ordered[0])
        if percentile >= 100:
            return float(ordered[-1])
        idx = int(round((len(ordered) - 1) * (percentile / 100.0)))
        idx = max(0, min(len(ordered) - 1, idx))
        return float(ordered[idx])

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
            existing.conflict_status = CONFLICT_STATUS_PENDING
            existing.requires_confirmation = True
            existing.conflict_updated_at = datetime.now()
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
