"""
Approval persistence and resolution helpers.
"""

from __future__ import annotations

import logging
import asyncio
import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ..execution.tool_executor import ToolExecutionRequest
from ..storage.persistence import StorageManager, get_storage_manager

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    """Approval lifecycle state."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalRecord(BaseModel):
    """Persisted approval request."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    status: ApprovalStatus = ApprovalStatus.PENDING
    workflow_id: Optional[str] = None
    execution_id: Optional[str] = None
    node_id: Optional[str] = None
    node_name: Optional[str] = None
    tool_call_id: str
    tool_name: str
    tool_source: str = "custom"
    arguments: Dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = False
    is_dangerous: bool = False
    reason: Optional[str] = None
    requested_by: Optional[str] = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    decision_by: Optional[str] = None
    decision_comment: Optional[str] = None


class ApprovalService:
    """Approval CRUD and decision service."""

    def __init__(self, storage: Optional[StorageManager] = None):
        self.storage = storage
        self._resolve_lock = asyncio.Lock()

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        """Create or reuse a pending approval request for the same tool call."""
        existing = await self.find_by_tool_call_id(approval.tool_call_id)
        if existing and existing.status == ApprovalStatus.PENDING:
            return existing

        approval.updated_at = approval.requested_at
        manager = await self._get_storage()
        await manager.save_approval(approval.model_dump(mode="json"))
        return approval

    async def create_from_tool_request(
        self,
        request: ToolExecutionRequest,
        *,
        reason: str = "approval_required",
    ) -> ApprovalRecord:
        """Persist an approval request based on a blocked tool execution."""
        metadata = request.execution_context.metadata
        shared_state = request.execution_context.shared_state
        approval = ApprovalRecord(
            id=self._stable_tool_call_approval_id(request.tool_call.id),
            workflow_id=metadata.get("workflow_id") or shared_state.get("workflow_id"),
            execution_id=request.execution_context.execution_id or metadata.get(
                "execution_id"
            ),
            node_id=request.node.id,
            node_name=request.node.name,
            tool_call_id=request.tool_call.id,
            tool_name=request.tool.name,
            tool_source=request.tool.source,
            arguments=request.tool_call.arguments,
            approval_required=request.tool.approval_required,
            is_dangerous=request.tool.is_dangerous,
            reason=reason,
            requested_by=metadata.get("requested_by"),
        )
        return await self.create_approval(approval)

    async def get_approval(self, approval_id: str) -> Optional[ApprovalRecord]:
        """Get a persisted approval by ID."""
        manager = await self._get_storage()
        data = await manager.load_approval(approval_id)
        return ApprovalRecord.model_validate(data) if data else None

    async def list_approvals(
        self,
        *,
        status: Optional[ApprovalStatus] = None,
        workflow_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> List[ApprovalRecord]:
        """List persisted approvals with simple in-memory filtering."""
        manager = await self._get_storage()
        approvals = [
            ApprovalRecord.model_validate(item)
            for item in await manager.list_approvals()
        ]

        if status is not None:
            approvals = [item for item in approvals if item.status == status]
        if workflow_id is not None:
            approvals = [item for item in approvals if item.workflow_id == workflow_id]
        if execution_id is not None:
            approvals = [
                item for item in approvals if item.execution_id == execution_id
            ]
        if tool_name is not None:
            approvals = [item for item in approvals if item.tool_name == tool_name]

        approvals.sort(key=lambda item: item.updated_at, reverse=True)
        return approvals

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        actor: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> ApprovalRecord:
        """Approve or deny a pending approval request."""
        async with self._resolve_lock:
            approval = await self.get_approval(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            if approval.status != ApprovalStatus.PENDING:
                raise ValueError(f"Approval '{approval_id}' is already resolved")

            now = datetime.now(timezone.utc)
            approval.status = (
                ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
            )
            approval.updated_at = now
            approval.resolved_at = now
            approval.decision_by = actor
            approval.decision_comment = comment

            manager = await self._get_storage()
            resolved_payload = approval.model_dump(mode="json")
            ok = await manager.resolve_approval_if_pending(resolved_payload)
            if not ok:
                latest = await self.get_approval(approval_id)
                if latest is None:
                    raise KeyError(approval_id)
                if latest.status != ApprovalStatus.PENDING:
                    raise ValueError(f"Approval '{approval_id}' is already resolved")
                raise ValueError(
                    f"Approval '{approval_id}' changed concurrently; retry resolution"
                )
            return approval

    async def find_by_tool_call_id(self, tool_call_id: str) -> Optional[ApprovalRecord]:
        """Find the latest approval for a tool call."""
        approvals = await self.list_approvals()
        for approval in approvals:
            if approval.tool_call_id == tool_call_id:
                return approval
        return None

    async def is_tool_call_approved(self, tool_call_id: str) -> bool:
        """Whether a tool call has a persisted approved decision."""
        approval = await self.find_by_tool_call_id(tool_call_id)
        return bool(approval and approval.status == ApprovalStatus.APPROVED)

    async def _get_storage(self) -> StorageManager:
        manager = self.storage or get_storage_manager()
        await manager.initialize()
        return manager

    @staticmethod
    def _stable_tool_call_approval_id(tool_call_id: str) -> str:
        """Build deterministic approval id for a tool call (cross-instance de-dup)."""
        digest = hashlib.sha256(tool_call_id.encode("utf-8")).hexdigest()[:24]
        return f"approval-tc-{digest}"


_approval_service: Optional[ApprovalService] = None


def get_approval_service() -> ApprovalService:
    """Get the process-wide approval service."""
    global _approval_service
    if _approval_service is None:
        _approval_service = ApprovalService()
    return _approval_service
