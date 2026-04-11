import argparse
import asyncio
import importlib.util
import pathlib


def _load_retrieval_gate_module():
    root = pathlib.Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "retrieval_gate.py"
    spec = importlib.util.spec_from_file_location("retrieval_gate", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_percentile_basic():
    module = _load_retrieval_gate_module()
    samples = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert module._percentile(samples, 95) == 50.0
    assert module._percentile(samples, 50) == 30.0


def test_retrieval_gate_pass_and_fail(tmp_path):
    module = _load_retrieval_gate_module()

    pass_args = argparse.Namespace(
        sessions=40,
        messages_per_session=20,
        queries=8,
        top_k=6,
        keyword="预算",
        max_p95_ms=2000.0,
        min_hit_rate=1.0,
        seed=7,
        storage_path=str(tmp_path / "pass"),
    )
    pass_result = asyncio.run(module.run_gate(pass_args))
    assert pass_result["passed"] is True
    assert pass_result["metrics"]["hit_rate"] >= 1.0

    fail_args = argparse.Namespace(
        sessions=20,
        messages_per_session=10,
        queries=5,
        top_k=4,
        keyword="预算",
        max_p95_ms=0.0,
        min_hit_rate=1.0,
        seed=7,
        storage_path=str(tmp_path / "fail"),
    )
    fail_result = asyncio.run(module.run_gate(fail_args))
    assert fail_result["passed"] is False
