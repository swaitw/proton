#!/usr/bin/env python3
"""
Quick self-check for Proton <-> MemPalace MCP wiring.

Usage:
  python scripts/check_mempalace_mcp.py
"""

from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _run(cmd: list[str], timeout: int = 12) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "").strip()


def main() -> int:
    print(f"python={sys.executable}")

    try:
        mod = importlib.import_module("mempalace.mcp_server")
        _ok(f"import mempalace.mcp_server -> {getattr(mod, '__file__', 'unknown')}")
    except Exception as e:
        _fail(f"import mempalace.mcp_server failed: {e}")
        print("Hint: install mempalace in the same environment as Proton.")
        return 2

    code, out = _run([sys.executable, "-m", "mempalace.mcp_server", "--help"])
    if code != 0:
        _fail("python -m mempalace.mcp_server --help failed")
        print(out[:500])
        return 3
    _ok("python -m mempalace.mcp_server --help")

    try:
        from src.portal.mempalace_client import MemPalaceClient
        import asyncio

        async def _probe() -> dict[str, Any]:
            c = MemPalaceClient(command=sys.executable, args=["-m", "mempalace.mcp_server"], max_retries=0)
            ready = await c.ensure_ready()
            names = c.tool_names()
            return {"ready": ready, "tools": names[:8], "count": len(names)}

        info = asyncio.run(_probe())
    except Exception as e:
        _fail(f"MemPalaceClient probe failed: {e}")
        return 4

    if not info.get("ready"):
        _fail("MemPalaceClient.ensure_ready() returned False")
        print(json.dumps(info, ensure_ascii=False))
        return 5

    _ok(f"MemPalaceClient ready with {info.get('count', 0)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
