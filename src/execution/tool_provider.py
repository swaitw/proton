import json
import logging
import os
from typing import Any, Dict, List, Optional, Protocol

from .tool_executor import ExecutableTool
from ..core.context import ExecutionContext
from ..core.models import BuiltinToolDefinition
from ..core.agent_node import AgentNode
from ..tools.registry import SystemToolRegistry, SystemTool
from ..plugins.registry import Tool as PluginTool

logger = logging.getLogger(__name__)

class ToolProvider(Protocol):
    """Protocol for providing tools to the unified executor."""
    
    def get_tools(self) -> List[ExecutableTool]:
        """Return a list of ExecutableTool instances."""
        ...


class SystemToolProvider:
    """Provider for hardcoded Python system tools (e.g. bash, fs)."""
    
    def __init__(
        self, 
        enabled_tool_names: List[str], 
        registry: SystemToolRegistry,
        agent_def: Optional[Any] = None,
        agent_node: Optional[AgentNode] = None,
    ):
        self._enabled_names = enabled_tool_names
        self._registry = registry
        self._agent_def = agent_def
        self._agent_node = agent_node
        
    def get_tools(self) -> List[ExecutableTool]:
        tools = []
        for tool_name in self._enabled_names:
            system_tool = self._registry.get(tool_name)
            if system_tool is None:
                logger.warning("Enabled system tool not found: %s", tool_name)
                continue
                
            tools.append(
                ExecutableTool(
                    name=system_tool.name,
                    description=system_tool.description,
                    parameters_schema=self._build_schema(system_tool),
                    handler=self._create_handler(system_tool),
                    source="system",
                    approval_required=system_tool.requires_approval,
                    is_dangerous=system_tool.is_dangerous,
                    metadata={"category": system_tool.category},
                )
            )
        return tools
        
    def _create_handler(self, system_tool: SystemTool):
        async def handler(params: Dict[str, Any], context: ExecutionContext) -> str:
            return await system_tool.execute(
                **params,
                __agent_definition=self._agent_def,
                __agent_node=self._agent_node,
                __execution_context=context,
            )
        return handler
        
    def _build_schema(self, system_tool: SystemTool) -> Dict[str, Any]:
        schema = system_tool.to_openai_schema()["function"]["parameters"]
        return self._normalize_schema(schema)

    @staticmethod
    def _normalize_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not schema:
            return {"type": "object", "properties": {}}
        if schema.get("type") == "object":
            normalized = dict(schema)
            normalized.setdefault("properties", {})
            normalized.setdefault("required", [])
            return normalized
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": True,
        }


class PluginToolProvider:
    """Provider for external plugins (MCP, Skills, RAG)."""
    
    def __init__(self, plugin_tools: List[PluginTool]):
        self._plugin_tools = plugin_tools
        
    def get_tools(self) -> List[ExecutableTool]:
        tools = []
        for plugin_tool in self._plugin_tools:
            tools.append(
                ExecutableTool(
                    name=plugin_tool.name,
                    description=plugin_tool.description,
                    parameters_schema=self._normalize_schema(plugin_tool.parameters_schema),
                    handler=self._create_handler(plugin_tool),
                    source=plugin_tool.source or "plugin",
                    approval_required=(
                        plugin_tool.approval_required
                        or bool(plugin_tool.metadata.get("approval_required"))
                    ),
                    is_dangerous=(
                        plugin_tool.is_dangerous
                        or bool(plugin_tool.metadata.get("is_dangerous"))
                    ),
                    metadata=plugin_tool.metadata.copy(),
                )
            )
        return tools
        
    def _create_handler(self, plugin_tool: PluginTool):
        async def handler(params: Dict[str, Any], _: ExecutionContext) -> Any:
            return await plugin_tool.execute(**params)
        return handler

    @staticmethod
    def _normalize_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not schema:
            return {"type": "object", "properties": {}}
        if schema.get("type") == "object":
            normalized = dict(schema)
            normalized.setdefault("properties", {})
            normalized.setdefault("required", [])
            return normalized
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": True,
        }


class BuiltinToolProvider:
    """Provider for UI-defined custom tools (HTTP, Code, Transform)."""
    
    def __init__(self, tool_defs: List[BuiltinToolDefinition], executor_callback):
        self._tool_defs = tool_defs
        # We need a callback to the adapter's _execute_tool since the actual 
        # HTTP/Code execution logic is still heavily tied to the adapter state.
        # In a perfect world, we would move the execution logic here too.
        self._executor_callback = executor_callback
        
    def get_tools(self) -> List[ExecutableTool]:
        tools = []
        for tool_def in self._tool_defs:
            tools.append(
                ExecutableTool(
                    name=tool_def.name,
                    description=tool_def.description,
                    parameters_schema=self._build_schema(tool_def),
                    handler=self._create_handler(tool_def),
                    source="builtin",
                    approval_required=tool_def.approval_required,
                    metadata={"tool_type": tool_def.tool_type},
                )
            )
        return tools
        
    def _create_handler(self, tool_def: BuiltinToolDefinition):
        async def handler(params: Dict[str, Any], _: ExecutionContext) -> str:
            return await self._executor_callback(tool_def, params)
        return handler
        
    def _build_schema(self, tool_def: BuiltinToolDefinition) -> Dict[str, Any]:
        properties = {}
        required = []

        for param in tool_def.parameters:
            param_schema: Dict[str, Any] = {
                "type": (
                    param.type.value if hasattr(param.type, "value") else str(param.type)
                ),
                "description": param.description,
            }
            if param.enum:
                param_schema["enum"] = param.enum
            if param.default is not None:
                param_schema["default"] = param.default

            properties[param.name] = param_schema
            if param.required:
                required.append(param.name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }
