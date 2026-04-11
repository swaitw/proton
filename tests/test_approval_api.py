import pathlib
import sys
import asyncio

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.api.main import create_app
from src.governance import approval as approval_module
from src.governance.approval import ApprovalRecord, ApprovalService, ApprovalStatus
from src.storage import persistence as persistence_module
from src.storage.persistence import FileStorageBackend, StorageManager


def test_approval_api_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTON_STORAGE_TYPE", "file")
    monkeypatch.setenv("PROTON_STORAGE_PATH", str(tmp_path))
    persistence_module._storage_manager = None
    approval_module._approval_service = None

    client = TestClient(create_app())

    create_response = client.post(
        "/api/approvals",
        json={
            "workflow_id": "wf-api",
            "execution_id": "exec-api",
            "node_id": "node-1",
            "node_name": "审批节点",
            "tool_call_id": "tc-api-1",
            "tool_name": "send_email",
            "tool_source": "system",
            "arguments": {"to": "demo@example.com"},
            "approval_required": True,
            "is_dangerous": True,
            "reason": "approval_required",
            "requested_by": "system",
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["status"] == "pending"
    approval_id = created["id"]

    list_response = client.get("/api/approvals", params={"status": "pending"})
    assert list_response.status_code == 200
    approvals = list_response.json()
    assert len(approvals) == 1
    assert approvals[0]["id"] == approval_id

    approve_response = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"actor": "qa-user", "comment": "looks safe"},
    )
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["status"] == "approved"
    assert approved["decision_by"] == "qa-user"
    assert approved["decision_comment"] == "looks safe"

    get_response = client.get(f"/api/approvals/{approval_id}")
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["status"] == "approved"

    deny_again_response = client.post(
        f"/api/approvals/{approval_id}/deny",
        json={"actor": "qa-user", "comment": "too late"},
    )
    assert deny_again_response.status_code == 409


def test_approval_service_concurrent_resolution(tmp_path):
    async def run_case():
        storage = StorageManager(FileStorageBackend(str(tmp_path)))
        await storage.initialize()
        service_a = ApprovalService(storage=storage)
        service_b = ApprovalService(storage=storage)

        approval = ApprovalRecord(
            tool_call_id="tc-concurrency",
            tool_name="shell_exec",
            tool_source="system",
            status=ApprovalStatus.PENDING,
            approval_required=True,
            is_dangerous=True,
            reason="approval_required",
        )
        created = await service_a.create_approval(approval)

        async def approve():
            return await service_a.resolve_approval(
                created.id,
                approved=True,
                actor="approver-a",
                comment="approve",
            )

        async def deny():
            return await service_b.resolve_approval(
                created.id,
                approved=False,
                actor="approver-b",
                comment="deny",
            )

        results = await asyncio.gather(
            approve(),
            deny(),
            return_exceptions=True,
        )

        success = [item for item in results if isinstance(item, ApprovalRecord)]
        errors = [item for item in results if isinstance(item, Exception)]

        assert len(success) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

        final = await service_a.get_approval(created.id)
        assert final is not None
        assert final.status in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}
        assert final.decision_by in {"approver-a", "approver-b"}

    asyncio.run(run_case())
