from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional


class MessageDeduplicator:
    def __init__(self, *, ttl_seconds: int = 300, max_size: int = 2048):
        self._ttl = ttl_seconds
        self._max = max_size
        self._items: "OrderedDict[str, float]" = OrderedDict()

    def seen(self, key: str) -> bool:
        now = time.time()
        self._gc(now)
        if key in self._items:
            self._items.move_to_end(key)
            return True
        self._items[key] = now
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return False

    def _gc(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._items:
            k, ts = next(iter(self._items.items()))
            if ts >= cutoff:
                break
            self._items.popitem(last=False)


class ChatQueueManager:
    def __init__(self, *, max_queue_size: int = 200, idle_seconds: int = 120):
        self._max_queue_size = max_queue_size
        self._idle_seconds = idle_seconds
        self._queues: Dict[str, asyncio.Queue] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, key: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
        async with self._lock:
            q = self._queues.get(key)
            if not q:
                q = asyncio.Queue(maxsize=self._max_queue_size)
                self._queues[key] = q
            if q.full():
                return
            q.put_nowait(coro_factory)
            if key not in self._tasks or self._tasks[key].done():
                self._tasks[key] = asyncio.create_task(self._worker(key))

    async def _worker(self, key: str) -> None:
        while True:
            q = self._queues.get(key)
            if not q:
                return
            try:
                factory = await asyncio.wait_for(q.get(), timeout=self._idle_seconds)
            except asyncio.TimeoutError:
                async with self._lock:
                    q2 = self._queues.get(key)
                    if q2 and q2.empty():
                        self._queues.pop(key, None)
                        self._tasks.pop(key, None)
                        return
                continue
            try:
                await factory()
            finally:
                q.task_done()


def ensure_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def generate_pairing_code(length: int = 8) -> str:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass
class PairingResult:
    paired: bool
    reason: str = ""


def is_sender_allowed(config: Dict[str, Any], sender_id: str) -> bool:
    allowed = ensure_list(config.get("allowed_users"))
    pairing_code = str(config.get("pairing_code") or "").strip()
    # Default-open mode: if neither allowlist nor pairing is configured, allow all users.
    if not allowed and not pairing_code:
        return True
    return sender_id in {str(u) for u in allowed}


def try_pair_sender(config: Dict[str, Any], sender_id: str, message_text: str) -> PairingResult:
    code = str(config.get("pairing_code") or "").strip()
    if not code:
        return PairingResult(paired=False, reason="pairing_disabled")
    expires_at = float(config.get("pairing_expires_at") or 0.0)
    if expires_at and time.time() > expires_at:
        return PairingResult(paired=False, reason="pairing_expired")
    code_norm = re.sub(r"[^A-Z0-9]", "", code.upper())
    text_norm = re.sub(r"[^A-Z0-9]", "", str(message_text or "").upper())
    if not code_norm or code_norm not in text_norm:
        return PairingResult(paired=False, reason="invalid_code")
    allowed = ensure_list(config.get("allowed_users"))
    if sender_id not in {str(u) for u in allowed}:
        allowed.append(sender_id)
        config["allowed_users"] = allowed
    return PairingResult(paired=True)
