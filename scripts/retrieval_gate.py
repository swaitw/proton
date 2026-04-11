#!/usr/bin/env python3
"""
检索压测门禁脚本。

默认基于 PortalService.search_sessions 做合成数据压测，
当 p95 延迟或命中率不达标时返回非 0 退出码，可用于 CI 门禁。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import PortalConversationMessage, PortalSession, SuperPortalConfig
from src.portal.service import PortalService
from src.storage.persistence import FileStorageBackend, StorageManager


class _NoopWorkflowManager:
    async def get_workflow(self, workflow_id: str):
        _ = workflow_id
        return None

    async def run_workflow(self, workflow_id: str, query: str):
        _ = workflow_id, query
        raise NotImplementedError


async def _build_dataset(
    service: PortalService,
    *,
    user_id: str,
    session_count: int,
    messages_per_session: int,
    keyword: str,
) -> None:
    for idx in range(session_count):
        messages: List[PortalConversationMessage] = []
        for j in range(messages_per_session):
            if j % 10 == 0:
                content = f"会话{idx} 的 {keyword} 方案评审与预算复盘"
            else:
                content = f"普通上下文 {idx}-{j}"
            messages.append(PortalConversationMessage(role="user", content=content))
        session = PortalSession(
            session_id=f"bench-session-{idx}",
            portal_id=service.config.id,
            user_id=user_id,
            messages=messages,
        )
        await service._save_session(session)


def _percentile(samples: List[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[rank]


async def run_gate(args: argparse.Namespace) -> Dict[str, Any]:
    storage_path = args.storage_path or tempfile.mkdtemp(prefix="proton-retrieval-gate-")
    storage = StorageManager(FileStorageBackend(storage_path))
    await storage.initialize()

    cfg = SuperPortalConfig(
        id="portal-retrieval-gate",
        name="retrieval-gate",
        workflow_ids=["wf-bench"],
        memory_enabled=False,
    )
    service = PortalService(
        config=cfg,
        workflow_manager=_NoopWorkflowManager(),
        storage=storage,
    )

    random.seed(args.seed)
    user_id = "gate-user"
    await _build_dataset(
        service,
        user_id=user_id,
        session_count=args.sessions,
        messages_per_session=args.messages_per_session,
        keyword=args.keyword,
    )

    # warmup
    await service.search_sessions(
        user_id=user_id,
        query=f"继续优化{args.keyword}",
        top_k=args.top_k,
    )

    latencies_ms: List[float] = []
    hit_count = 0
    for i in range(args.queries):
        query = f"第{i}轮继续讨论{args.keyword}与预算优化"
        start = time.perf_counter()
        results = await service.search_sessions(
            user_id=user_id,
            query=query,
            top_k=args.top_k,
            exclude_session_id=f"bench-session-{i % max(1, args.sessions)}",
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies_ms.append(elapsed_ms)

        if len(results) == args.top_k and all(args.keyword in item["snippet"] for item in results):
            hit_count += 1

    p95_ms = _percentile(latencies_ms, 95)
    avg_ms = statistics.mean(latencies_ms) if latencies_ms else 0.0
    hit_rate = (hit_count / args.queries) if args.queries > 0 else 0.0

    passed = p95_ms <= args.max_p95_ms and hit_rate >= args.min_hit_rate
    result = {
        "passed": passed,
        "config": {
            "sessions": args.sessions,
            "messages_per_session": args.messages_per_session,
            "queries": args.queries,
            "top_k": args.top_k,
            "keyword": args.keyword,
            "max_p95_ms": args.max_p95_ms,
            "min_hit_rate": args.min_hit_rate,
            "storage_path": storage_path,
        },
        "metrics": {
            "avg_ms": round(avg_ms, 2),
            "p95_ms": round(p95_ms, 2),
            "hit_rate": round(hit_rate, 4),
        },
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proton 检索压测门禁")
    parser.add_argument("--sessions", type=int, default=300)
    parser.add_argument("--messages-per-session", type=int, default=40)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--keyword", type=str, default="预算")
    parser.add_argument("--max-p95-ms", type=float, default=150.0)
    parser.add_argument("--min-hit-rate", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--storage-path", type=str, default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = asyncio.run(run_gate(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
