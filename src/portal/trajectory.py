"""
Trajectory Pool — lightweight signal accumulator for L1→L2→L3 precipitation.

L1: Per-turn signal extraction (called after each chat completion)
L2: Periodic learning cycle (triggered when pool reaches threshold)
L3: Immediate precipitation (triggered by strong user signals)
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Strong signal keywords that trigger immediate L3 precipitation
STRONG_SIGNAL_KEYWORDS: List[str] = [
    "保存这个",
    "以后还会用",
    "每次都要",
    "记住这个",
    "下次还要",
    "固定流程",
    "标准操作",
    "总是这样做",
    "每次都这样",
    "存为模板",
    "save this",
    "remember this",
    "always do this",
    "template this",
]

# Pool trigger thresholds
DEFAULT_POOL_SIZE_THRESHOLD = 20
DEFAULT_POOL_TIME_THRESHOLD_SECONDS = 3600  # 1 hour


@dataclass
class TrajectoryEntry:
    """A single trajectory signal from one chat turn."""
    session_id: str
    signals: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class TrajectoryPool:
    """
    Accumulates trajectory entries and triggers L2 learning cycles
    when thresholds are reached.

    Thread-safe via a simple lock (trajectories may be added from
    asyncio.create_task background coroutines running concurrently).
    """

    def __init__(
        self,
        *,
        size_threshold: int = DEFAULT_POOL_SIZE_THRESHOLD,
        time_threshold_seconds: float = DEFAULT_POOL_TIME_THRESHOLD_SECONDS,
    ):
        self._entries: List[TrajectoryEntry] = []
        self._lock = threading.Lock()
        self._last_cycle_time: float = time.time()
        self._size_threshold = size_threshold
        self._time_threshold_seconds = time_threshold_seconds

    def add(self, session_id: str, signals: Dict[str, Any]) -> None:
        """Add a trajectory entry to the pool."""
        entry = TrajectoryEntry(session_id=session_id, signals=signals)
        with self._lock:
            self._entries.append(entry)
        logger.debug(
            f"[TrajectoryPool] Added entry for session {session_id}, "
            f"pool size={len(self._entries)}"
        )

    def should_trigger_learning(self) -> bool:
        """
        Check whether the pool has reached a trigger threshold.

        Triggers when:
        - Pool has >= size_threshold entries, OR
        - >= time_threshold_seconds have elapsed since last cycle
        """
        with self._lock:
            if len(self._entries) >= self._size_threshold:
                return True
            elapsed = time.time() - self._last_cycle_time
            if elapsed >= self._time_threshold_seconds and len(self._entries) > 0:
                return True
        return False

    def drain(self) -> List[TrajectoryEntry]:
        """
        Take out all entries and reset the cycle timer.

        Returns the drained entries (caller owns them).
        """
        with self._lock:
            entries = list(self._entries)
            self._entries.clear()
            self._last_cycle_time = time.time()
        logger.info(f"[TrajectoryPool] Drained {len(entries)} entries")
        return entries

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)


def has_strong_signal(text: str, keywords: Optional[List[str]] = None) -> bool:
    """Check if the text contains any strong signal keyword."""
    kws = keywords or STRONG_SIGNAL_KEYWORDS
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in kws)


# Global singleton for trajectory pool
_global_trajectory_pool: Optional[TrajectoryPool] = None


def get_trajectory_pool() -> TrajectoryPool:
    """Get the global TrajectoryPool singleton."""
    global _global_trajectory_pool
    if _global_trajectory_pool is None:
        _global_trajectory_pool = TrajectoryPool()
    return _global_trajectory_pool
