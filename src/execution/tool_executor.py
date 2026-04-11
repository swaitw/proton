"""
Unified tool executor for built-in, system, and plugin tools.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

from ..core.agent_node import AgentNode
from ..core.context import ExecutionContext
from ..core.models import ToolCall, ToolResult

logger = logging.getLogger(__name__)


ToolHandler = Callable[[Dict[str, Any], ExecutionContext], Awaitable[Any]]


@dataclass
class ExecutableTool:
    """Unified executable tool definition."""

    name: str
    description: str
    parameters_schema: Dict[str, Any]
    handler: ToolHandler
    source: str = "custom"
    approval_required: bool = False
    is_dangerous: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling schema."""
        parameters = self.parameters_schema or {"type": "object", "properties": {}}
        if parameters.get("type") != "object":
            parameters = {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


@dataclass
class ToolExecutionRequest:
    """Context for a single tool execution."""

    node: AgentNode
    tool: ExecutableTool
    tool_call: ToolCall
    execution_context: ExecutionContext
    runtime_metadata: Dict[str, Any] = field(default_factory=dict)


class ToolExecutionSlice(Protocol):
    """Slice protocol for intercepting tool execution."""

    async def before_execute(
        self,
        request: ToolExecutionRequest,
    ) -> Optional[ToolResult]:
        """Run before execution. Return ToolResult to short-circuit."""
        ...

    async def after_execute(
        self,
        request: ToolExecutionRequest,
        result: ToolResult,
    ) -> ToolResult:
        """Run after execution and optionally mutate the result."""
        ...


class ToolExecutor:
    """
    Unified executor for the three tool surfaces used by Proton.

    It normalizes:
    - built-in visual tools
    - system tools
    - plugin tools (MCP / skill / RAG)
    """

    def __init__(
        self,
        node: AgentNode,
        slices: Optional[List[ToolExecutionSlice]] = None,
    ):
        self.node = node
        self._tools: Dict[str, ExecutableTool] = {}
        self._slices = slices or []

    def register_tool(self, tool: ExecutableTool) -> None:
        """Register a unified tool."""
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> Optional[ExecutableTool]:
        """Get tool definition by name."""
        return self._tools.get(tool_name)

    def list_tools(self) -> List[ExecutableTool]:
        """List all registered tools."""
        return list(self._tools.values())

    def get_openai_schemas(self) -> List[Dict[str, Any]]:
        """Get all tools as OpenAI function-calling schemas."""
        return [tool.to_openai_schema() for tool in self._tools.values()]

    async def execute(
        self,
        *,
        tool_call: ToolCall,
        context: ExecutionContext,
    ) -> ToolResult:
        """Execute a tool call through governance slices."""
        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )

        request = ToolExecutionRequest(
            node=self.node,
            tool=tool,
            tool_call=tool_call,
            execution_context=context,
        )

        for slice_impl in self._slices:
            short_circuit = await slice_impl.before_execute(request)
            if short_circuit is not None:
                return short_circuit

        try:
            raw_result = await tool.handler(tool_call.arguments, context)
            result = ToolResult(
                tool_call_id=tool_call.id,
                content=self._normalize_result(raw_result),
                is_error=False,
            )
        except Exception as e:
            logger.error(
                "Tool execution failed for %s on node %s: %s",
                tool_call.name,
                self.node.id,
                e,
            )
            result = ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing tool: {str(e)}",
                is_error=True,
            )

        for slice_impl in reversed(self._slices):
            result = await slice_impl.after_execute(request, result)

        return result

    @staticmethod
    def _normalize_result(result: Any) -> str:
        """Normalize arbitrary tool results to text."""
        if result is None:
            return ""
        if isinstance(result, str):
            return result
        if isinstance(result, (dict, list, tuple, bool, int, float)):
            return json.dumps(result, ensure_ascii=False)
        return str(result)
