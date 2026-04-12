#!/usr/bin/env python3
"""
记忆检索质量门禁脚本。

构造一组语义变体查询，验证 Recall@K / MRR / P95。
用于 CI 中对记忆检索质量做回归约束。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.portal.memory import PortalMemoryManager
from src.storage.persistence import FileStorageBackend, StorageManager


def _percentile(samples: List[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[rank]


async def _seed_memories(memory: PortalMemoryManager, portal_id: str, user_id: str) -> None:
    samples = [
        ("差旅报销流程：先提交费用单再财务审批", "context", 0.88),
        ("用户偏好：邮件回复语气保持正式简洁", "preference", 0.82),
        ("项目预算为120万人民币", "fact", 0.91),
        ("会议纪要通常在周五统一归档", "context", 0.72),
        ("用户偏好：先给结论再给细节", "preference", 0.79),
    ]
    for content, memory_type, importance in samples:
        await memory.add(
            portal_id=portal_id,
            user_id=user_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
        )


def _cases() -> List[Dict[str, Any]]:
    return [
        {
            "query": "reimbursement 怎么走？",
            "intent": "task_continuation",
            "must_contain": "差旅报销流程",
        },
        {
            "query": "用户喜欢什么回复风格",
            "intent": "preference_lookup",
            "must_contain": "用户偏好：邮件回复语气保持正式简洁",
        },
        {
            "query": "项目经费是多少",
            "intent": "fact_lookup",
            "must_contain": "项目预算为120万人民币",
        },
        {
            "query": "会后记录什么时候整理",
            "intent": "task_continuation",
            "must_contain": "会议纪要通常在周五统一归档",
        },
    ]


async def run_gate(args: argparse.Namespace) -> Dict[str, Any]:
    storage_path = args.storage_path or tempfile.mkdtemp(prefix="proton-memory-quality-")
    storage = StorageManager(FileStorageBackend(storage_path))
    await storage.initialize()
    memory = PortalMemoryManager(storage)

    portal_id = "quality-portal"
    user_id = "quality-user"
    await _seed_memories(memory, portal_id, user_id)

    queries = _cases()
    latencies: List[float] = []
    hit_count = 0
    reciprocal_rank_sum = 0.0

    for case in queries:
        start = time.perf_counter()
        result = await memory.retrieve(
            portal_id=portal_id,
            user_id=user_id,
            query=case["query"],
            query_intent=case["intent"],
            top_k=args.top_k,
        )
        latencies.append((time.perf_counter() - start) * 1000)

        target = case["must_contain"]
        rank = 0
        for idx, item in enumerate(result, start=1):
            if target in item.content:
                rank = idx
                break
        if rank > 0:
            hit_count += 1
            reciprocal_rank_sum += 1.0 / rank

    total = len(queries)
    recall_at_k = (hit_count / total) if total else 0.0
    mrr = (reciprocal_rank_sum / total) if total else 0.0
    p95_ms = _percentile(latencies, 95)
    avg_ms = statistics.mean(latencies) if latencies else 0.0

    passed = (
        recall_at_k >= args.min_recall_at_k
        and mrr >= args.min_mrr
        and p95_ms <= args.max_p95_ms
    )
    return {
        "passed": passed,
        "config": {
            "top_k": args.top_k,
            "min_recall_at_k": args.min_recall_at_k,
            "min_mrr": args.min_mrr,
            "max_p95_ms": args.max_p95_ms,
            "storage_path": storage_path,
        },
        "metrics": {
            "recall_at_k": round(recall_at_k, 4),
            "mrr": round(mrr, 4),
            "avg_ms": round(avg_ms, 2),
            "p95_ms": round(p95_ms, 2),
            "queries": total,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proton 记忆检索质量门禁")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-recall-at-k", type=float, default=0.75)
    parser.add_argument("--min-mrr", type=float, default=0.55)
    parser.add_argument("--max-p95-ms", type=float, default=200.0)
    parser.add_argument("--storage-path", type=str, default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = asyncio.run(run_gate(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
