"""
Tool governance slice for approval enforcement and audit trails.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Dict, Iterable, Optional

from ..core.models import ToolResult
from ..execution.tool_executor import ToolExecutionRequest, ToolExecutionSlice
from .approval import ApprovalService, get_approval_service
from .policy_engine import PolicyAction, ToolPolicyEngine

logger = logging.getLogger(__name__)


class ToolGovernanceSlice(ToolExecutionSlice):
    """Enforces approval rules and records tool execution audit data."""

    def __init__(
        self,
        *,
        audit_key: str = "tool_execution_audit",
        approved_tools_key: str = "approved_tools",
        approved_tool_calls_key: str = "approved_tool_calls",
        enforce_dangerous_tool_approval: bool = True,
        approval_service: Optional[ApprovalService] = None,
        persist_approval_requests: bool = True,
        policy_engine: Optional[ToolPolicyEngine] = None,
    ):
        self.audit_key = audit_key
        self.approved_tools_key = approved_tools_key
        self.approved_tool_calls_key = approved_tool_calls_key
        self.enforce_dangerous_tool_approval = enforce_dangerous_tool_approval
        self.approval_service = approval_service
        self.persist_approval_requests = persist_approval_requests
        self.policy_engine = policy_engine or ToolPolicyEngine()

    async def before_execute(
        self,
        request: ToolExecutionRequest,
    ) -> Optional[ToolResult]:
        policy_decision = self.policy_engine.evaluate(request)
        if policy_decision.metadata:
            request.runtime_metadata["policy_decision"] = policy_decision.metadata

        if policy_decision.action == PolicyAction.ALLOW:
            return None

        if policy_decision.action == PolicyAction.DENY:
            result = ToolResult(
                tool_call_id=request.tool_call.id,
                content=(
                    f"Denied by policy for tool '{request.tool.name}' "
                    f"({request.tool.source})"
                ),
                is_error=True,
                metadata={
                    "approval_status": "denied",
                    "reason": policy_decision.reason,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                    **policy_decision.metadata,
                },
            )
            self._append_audit(
                request,
                status="denied",
                result=result,
                reason=policy_decision.reason,
            )
            return result

        if await self._is_approved(request):
            return None

        approval_id = await self._persist_approval_request(request)
        content = (
            f"Approval required for tool '{request.tool.name}' "
            f"({request.tool.source})"
        )
        if approval_id:
            content = f"{content} [approval_id={approval_id}]"

        result = ToolResult(
            tool_call_id=request.tool_call.id,
            content=content,
            is_error=True,
            metadata={
                "approval_status": "pending",
                "approval_id": approval_id,
                "reason": policy_decision.reason,
                "tool_name": request.tool.name,
                "tool_source": request.tool.source,
                **policy_decision.metadata,
            },
        )
        self._append_audit(
            request,
            status="blocked",
            result=result,
            reason="approval_required",
            approval_id=approval_id,
        )
        return result

    async def after_execute(
        self,
        request: ToolExecutionRequest,
        result: ToolResult,
    ) -> ToolResult:
        approval_resolution = request.runtime_metadata.get("approval_resolution")
        if approval_resolution:
            result.metadata.update(approval_resolution)
        self._append_audit(
            request,
            status="error" if result.is_error else "completed",
            result=result,
        )
        return result

    async def _is_approved(self, request: ToolExecutionRequest) -> bool:
        tool_name = request.tool.name
        tool_call_id = request.tool_call.id
        approval_sources = [
            request.execution_context.metadata,
            request.execution_context.shared_state,
        ]

        for source in approval_sources:
            approved_tools = self._as_iterable(source.get(self.approved_tools_key))
            for pattern in approved_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    request.runtime_metadata["approval_resolution"] = {
                        "approval_status": "approved",
                        "approval_source": "runtime_tool",
                        "tool_name": request.tool.name,
                        "tool_source": request.tool.source,
                        "matched_pattern": pattern,
                    }
                    return True

            approved_tool_calls = self._as_iterable(
                source.get(self.approved_tool_calls_key)
            )
            if tool_call_id in approved_tool_calls:
                request.runtime_metadata["approval_resolution"] = {
                    "approval_status": "approved",
                    "approval_source": "runtime_tool_call",
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                }
                return True

        service = self.approval_service or get_approval_service()
        try:
            approval = await service.find_by_tool_call_id(tool_call_id)
            if approval and approval.status.value == "approved":
                request.runtime_metadata["approval_resolution"] = {
                    "approval_status": "approved",
                    "approval_source": "persisted",
                    "approval_id": approval.id,
                    "decision_by": approval.decision_by,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                }
                return True
            return False
        except Exception as exc:
            logger.warning(
                "Failed to load persisted approval for tool_call_id=%s: %s",
                tool_call_id,
                exc,
            )
        return False

    @staticmethod
    def _as_iterable(value: Any) -> Iterable[Any]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple, set)):
            return value
        return (value,)

    def _append_audit(
        self,
        request: ToolExecutionRequest,
        *,
        status: str,
        result: ToolResult,
        reason: Optional[str] = None,
        approval_id: Optional[str] = None,
    ) -> None:
        audit_log = request.execution_context.metadata.setdefault(self.audit_key, [])
        entry: Dict[str, Any] = {
            "tool_call_id": request.tool_call.id,
            "tool_name": request.tool.name,
            "tool_source": request.tool.source,
            "status": status,
            "is_error": result.is_error,
            "approval_required": request.tool.approval_required,
            "is_dangerous": request.tool.is_dangerous,
            "arguments": request.tool_call.arguments,
            "result_preview": result.content[:500],
        }
        if reason:
            entry["reason"] = reason
        if approval_id:
            entry["approval_id"] = approval_id
        audit_log.append(entry)

    async def _persist_approval_request(
        self,
        request: ToolExecutionRequest,
    ) -> Optional[str]:
        if not self.persist_approval_requests:
            return None

        service = self.approval_service or get_approval_service()
        try:
            approval = await service.create_from_tool_request(request)
            return approval.id
        except Exception as exc:
            logger.warning(
                "Failed to persist approval request for tool_call_id=%s: %s",
                request.tool_call.id,
                exc,
            )
            return None
