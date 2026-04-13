from __future__ import annotations

import asyncio
import re
import time
import logging
from typing import Any, Dict, Optional

from ..core.models import MCPServerConfig
from ..plugins.mcp_plugin import MCPPlugin
from ..core.models import PluginConfig

logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-").lower() or "general"


class MemPalaceClient:
    def __init__(
        self,
        *,
        palace_path: Optional[str] = None,
        command: str = "mempalace",
        args: Optional[list[str]] = None,
        env: Optional[Dict[str, str]] = None,
        max_retries: int = 2,
        retry_backoff_s: float = 0.35,
        unhealthy_cooldown_s: float = 3.0,
    ):
        self._command = command
        self._args = args or ["-m", "mempalace.mcp_server"]
        self._env = env or {}
        if palace_path:
            self._env["MEMPALACE_PALACE_PATH"] = palace_path
        self._plugin: Optional[MCPPlugin] = None
        self._ready = False
        self._lock = asyncio.Lock()
        self._call_lock = asyncio.Lock()
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_s = max(0.0, float(retry_backoff_s))
        self._unhealthy_cooldown_s = max(0.0, float(unhealthy_cooldown_s))
        self._last_init_error: Optional[str] = None
        self._unhealthy_until: float = 0.0

    async def ensure_ready(self) -> bool:
        if self._ready and self._plugin:
            return True
        now = time.time()
        if now < self._unhealthy_until:
            return False
        async with self._lock:
            if self._ready and self._plugin:
                return True
            now = time.time()
            if now < self._unhealthy_until:
                return False
            cfg = MCPServerConfig(
                name="mempalace",
                command=self._command,
                args=self._args,
                env=self._env,
                transport="stdio",
            )
            plugin_cfg = PluginConfig(type="mcp", enabled=True, mcp_config=cfg)
            plugin = MCPPlugin(plugin_cfg)
            
            for attempt in range(self._max_retries + 1):
                try:
                    await asyncio.wait_for(plugin.initialize(), timeout=10.0)
                    if not plugin.get_tools():
                        raise RuntimeError("MCP connected but no tools were discovered")
                    self._plugin = plugin
                    self._ready = True
                    self._last_init_error = None
                    self._unhealthy_until = 0.0
                    return True
                except Exception as e:
                    logger.warning("MemPalace MCP init attempt %d failed: %s", attempt + 1, e)
                    try:
                        await plugin.cleanup()
                    except Exception:
                        pass
                        
                    if attempt < self._max_retries:
                        await asyncio.sleep(self._retry_backoff_s * (2 ** attempt))
                    else:
                        self._ready = False
                        self._plugin = None
                        self._last_init_error = str(e)
                        self._unhealthy_until = time.time() + self._unhealthy_cooldown_s
                        logger.error(
                            "MemPalace MCP init exhausted (command=%s args=%s): %s",
                            self._command,
                            self._args,
                            e,
                        )
                        return False

    async def health_check(self) -> bool:
        ok = await self.ensure_ready()
        if not ok:
            return False
        try:
            await self.call_any(["mempalace_status", "status"], {})
            return True
        except Exception:
            return False

    def tool_names(self) -> list[str]:
        if not self._plugin:
            return []
        return [t.name for t in self._plugin.get_tools()]

    def resolve_tool_name(self, logical_name: str) -> str:
        """
        Return the exact tool name as specified in the MemPalace document.
        """
        return logical_name

    async def call(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        start = time.time()
        for attempt in range(self._max_retries + 1):
            ok = await self.ensure_ready()
            if not ok or not self._plugin:
                raise RuntimeError(
                    f"MemPalace MCP is unavailable: {self._last_init_error or 'init not completed'}"
                )
            try:
                async with self._call_lock:
                    result = await self._plugin.call_tool(tool_name, arguments)
                took_ms = int((time.time() - start) * 1000)
                logger.debug("MemPalace tool=%s ok took_ms=%s", tool_name, took_ms)
                return result
            except Exception as e:
                took_ms = int((time.time() - start) * 1000)
                logger.warning(
                    "MemPalace tool=%s failed attempt=%s took_ms=%s err=%s",
                    tool_name,
                    attempt + 1,
                    took_ms,
                    e,
                )
                await self._reset_connection()
                if attempt >= self._max_retries:
                    raise
                await asyncio.sleep(self._retry_backoff_s * (2**attempt))

        raise RuntimeError("MemPalace call retry loop exhausted")

    async def call_any(self, tool_names: list[str], arguments: Dict[str, Any]) -> Any:
        last_error: Optional[Exception] = None
        for name in tool_names:
            try:
                return await self.call(name, arguments)
            except Exception as e:
                last_error = e
        if last_error:
            raise last_error
        raise RuntimeError("No tool names provided")

    async def _reset_connection(self) -> None:
        async with self._lock:
            if self._plugin:
                try:
                    await self._plugin.cleanup()
                except Exception:
                    pass
            self._plugin = None
            self._ready = False
            self._unhealthy_until = 0.0

    @staticmethod
    def build_wing(portal_id: str, user_id: str, strategy: str) -> str:
        s = (strategy or "per_user").strip().lower()
        if s == "per_portal":
            return _slugify(f"proton_portal_{portal_id}")
        if s == "shared":
            return "proton_shared"
        return _slugify(f"proton_user_{user_id}")

    @staticmethod
    def build_room(portal_id: str, default_room: str) -> str:
        if default_room:
            return _slugify(default_room)
        return _slugify(f"portal_{portal_id}")
