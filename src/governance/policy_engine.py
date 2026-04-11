"""
Policy engine for tool approval and deny decisions.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List

from ..execution.tool_executor import ToolExecutionRequest


class PolicyAction(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class PolicyDecision:
    action: PolicyAction
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolPolicyEngine:
    """
    Unified policy evaluator inspired by OpenClaw/Hermes defaults.

    Supported controls:
    - dm_policy: pairing/open
    - sender_paired: whether requester is paired/authorized
    - deny_tools / allow_tools / require_approval_tools
    - dangerous_tool_requires_approval
    - command/url/path pattern deny + approval rules
    """

    def evaluate(self, request: ToolExecutionRequest) -> PolicyDecision:
        policy = self._read_policy(request)
        args = request.tool_call.arguments or {}

        dm_policy = str(policy.get("dm_policy", "pairing")).lower()
        sender_paired = bool(policy.get("sender_paired", True))
        if dm_policy == "pairing" and not sender_paired:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason="dm_pairing_required",
                metadata={
                    "policy_source": "channel_pairing",
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        tool_name = request.tool.name
        deny_tools = set(self._to_str_list(policy.get("deny_tools")))
        allow_tools = set(self._to_str_list(policy.get("allow_tools")))
        require_approval_tools = set(
            self._to_str_list(policy.get("require_approval_tools"))
        )

        if tool_name in deny_tools:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason="tool_denied_by_policy",
                metadata={
                    "policy_source": "deny_tools",
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if tool_name in allow_tools:
            return PolicyDecision(
                action=PolicyAction.ALLOW,
                reason="tool_allowed_by_policy",
                metadata={
                    "policy_source": "allow_tools",
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        command = self._string_value(args.get("command"))
        url = self._string_value(args.get("url"))
        path = self._string_value(
            args.get("path") or args.get("file_path") or args.get("working_dir")
        )

        deny_command_patterns = self._to_str_list(policy.get("deny_command_patterns"))
        require_command_patterns = self._to_str_list(
            policy.get("require_approval_command_patterns")
        )
        deny_url_patterns = self._to_str_list(policy.get("deny_url_patterns"))
        require_url_patterns = self._to_str_list(policy.get("require_approval_url_patterns"))
        deny_path_patterns = self._to_str_list(policy.get("deny_path_patterns"))
        require_path_patterns = self._to_str_list(
            policy.get("require_approval_path_patterns")
        )

        if command and self._matches_any(command, deny_command_patterns):
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason="command_denied_by_policy",
                metadata={
                    "policy_source": "deny_command_patterns",
                    "command": command,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if url and self._matches_any(url, deny_url_patterns):
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason="url_denied_by_policy",
                metadata={
                    "policy_source": "deny_url_patterns",
                    "url": url,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if path and self._matches_any(path, deny_path_patterns):
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason="path_denied_by_policy",
                metadata={
                    "policy_source": "deny_path_patterns",
                    "path": path,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if tool_name in require_approval_tools:
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                reason="tool_requires_approval_by_policy",
                metadata={
                    "policy_source": "require_approval_tools",
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if command and self._matches_any(command, require_command_patterns):
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                reason="command_requires_approval_by_policy",
                metadata={
                    "policy_source": "require_approval_command_patterns",
                    "command": command,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if url and self._matches_any(url, require_url_patterns):
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                reason="url_requires_approval_by_policy",
                metadata={
                    "policy_source": "require_approval_url_patterns",
                    "url": url,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        if path and self._matches_any(path, require_path_patterns):
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                reason="path_requires_approval_by_policy",
                metadata={
                    "policy_source": "require_approval_path_patterns",
                    "path": path,
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        dangerous_requires_approval = bool(
            policy.get("dangerous_tool_requires_approval", True)
        )
        if request.tool.approval_required or (
            dangerous_requires_approval and request.tool.is_dangerous
        ):
            return PolicyDecision(
                action=PolicyAction.REQUIRE_APPROVAL,
                reason="tool_requires_approval",
                metadata={
                    "policy_source": "tool_defaults",
                    "tool_name": request.tool.name,
                    "tool_source": request.tool.source,
                },
            )

        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason="allowed_by_default",
            metadata={
                "policy_source": "default_allow",
                "tool_name": request.tool.name,
                "tool_source": request.tool.source,
            },
        )

    def _read_policy(self, request: ToolExecutionRequest) -> Dict[str, Any]:
        metadata_policy = request.execution_context.metadata.get("approval_policy", {})
        shared_policy = request.execution_context.shared_state.get("approval_policy", {})
        policy: Dict[str, Any] = {}
        if isinstance(shared_policy, dict):
            policy.update(shared_policy)
        if isinstance(metadata_policy, dict):
            policy.update(metadata_policy)
        return policy

    @staticmethod
    def _to_str_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Iterable):
            result = []
            for item in value:
                if isinstance(item, str) and item:
                    result.append(item)
            return result
        return []

    @staticmethod
    def _string_value(value: Any) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _matches_any(candidate: str, patterns: List[str]) -> bool:
        value = candidate.lower()
        for pattern in patterns:
            normalized = pattern.lower()
            if fnmatch.fnmatch(value, normalized) or normalized in value:
                return True
        return False
