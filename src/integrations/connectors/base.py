from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from ..models import ChannelName, PortalChannelBinding, PortalChannelStatus

logger = logging.getLogger(__name__)


class Connector:
    def __init__(self, binding: PortalChannelBinding):
        self.binding = binding
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self._meta: Dict[str, Any] = {}
        self._healthy: bool = False
        self._last_success_at: Optional[float] = None

    @property
    def channel(self) -> ChannelName:
        return self.binding.channel

    def status(self) -> PortalChannelStatus:
        running = bool(self._task and not self._task.done())
        meta = dict(self._meta)
        meta.setdefault("running", running)
        meta.setdefault("healthy", self._healthy)
        if self._last_success_at is not None:
            meta.setdefault("last_success_at", self._last_success_at)
        return PortalChannelStatus(
            portal_id=self.binding.portal_id,
            channel=self.binding.channel,
            enabled=self.binding.enabled,
            connected=bool(running and self._healthy),
            last_error=self._last_error,
            meta=meta,
        )

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._healthy = False
        self._task = asyncio.create_task(self._run_wrapper())

    async def stop(self) -> None:
        self._running = False
        self._healthy = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_wrapper(self) -> None:
        retry = 0
        delays = [1, 2, 5, 10, 30]
        while self._running:
            try:
                self._meta["running"] = True
                self._meta["restart_count"] = retry
                await self.run()
                self._meta["running"] = False
                self._healthy = False
                return
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._last_error = str(e)
                self._healthy = False
                self._meta["running"] = False
                delay = delays[min(retry, len(delays) - 1)]
                logger.warning("[%s/%s] Connector crashed, retry in %ss: %s", self.binding.portal_id, self.binding.channel, delay, self._last_error)
                retry += 1
                await asyncio.sleep(delay)

    async def run(self) -> None:
        raise NotImplementedError

    def mark_success(self, meta: Optional[Dict[str, Any]] = None) -> None:
        self._healthy = True
        self._last_success_at = time.time()
        self._last_error = None
        if meta:
            self._meta.update(meta)
