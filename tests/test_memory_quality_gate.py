import argparse
import asyncio
import importlib.util
import pathlib


def _load_memory_quality_gate_module():
    root = pathlib.Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "memory_quality_gate.py"
    spec = importlib.util.spec_from_file_location("memory_quality_gate", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_quality_gate_pass_and_fail(tmp_path):
    module = _load_memory_quality_gate_module()

    pass_args = argparse.Namespace(
        top_k=3,
        min_recall_at_k=0.5,
        min_mrr=0.3,
        max_p95_ms=1000.0,
        storage_path=str(tmp_path / "pass"),
    )
    pass_result = asyncio.run(module.run_gate(pass_args))
    assert pass_result["passed"] is True
    assert pass_result["metrics"]["recall_at_k"] >= 0.5

    fail_args = argparse.Namespace(
        top_k=1,
        min_recall_at_k=1.0,
        min_mrr=1.0,
        max_p95_ms=0.0,
        storage_path=str(tmp_path / "fail"),
    )
    fail_result = asyncio.run(module.run_gate(fail_args))
    assert fail_result["passed"] is False
