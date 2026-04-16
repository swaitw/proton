"""
MCP (Model Context Protocol) Plugin implementation.

This plugin integrates MCP servers as tool providers for agents.
"""

import logging
import asyncio
import subprocess
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from .registry import Plugin, Tool
from ..core.models import PluginConfig, MCPServerConfig

logger = logging.getLogger(__name__)


class MCPPlugin(Plugin):
    """
    Plugin for integrating MCP servers.

    MCP servers provide tools that agents can use.
    This plugin manages the connection to MCP servers
    and exposes their tools in a unified format.
    """

    def __init__(self, config: PluginConfig):
        super().__init__(config)
        self._mcp_config: Optional[MCPServerConfig] = config.mcp_config
        self._server_process: Optional[subprocess.Popen] = None
        self._client = None
        self._mcp_available = False
        self._exit_stack: Optional[AsyncExitStack] = None

    async def initialize(self) -> None:
        """Initialize the MCP plugin."""
        if self._initialized:
            return

        if not self._mcp_config:
            raise ValueError("MCPServerConfig is required")

        # Try to import MCP
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            self._mcp_available = True
        except ImportError:
            logger.warning("MCP package not installed. Using fallback mode.")
            self._mcp_available = False
            self._initialized = True
            return

        # Start the MCP server based on transport type
        try:
            if self._mcp_config.transport == "stdio":
                await self._start_stdio_server()
            elif self._mcp_config.transport == "http":
                await self._connect_http_server()
            else:
                logger.warning(f"Unknown transport: {self._mcp_config.transport}")

            self._initialized = True
            logger.info(f"MCP plugin initialized: {self._mcp_config.name}")

        except Exception as e:
            logger.error(f"Failed to initialize MCP plugin: {e}")
            self._initialized = True  # Allow graceful degradation

    async def _start_stdio_server(self) -> None:
        """Start an MCP server using stdio transport."""
        cfg = self._mcp_config
        if not cfg:
            raise ValueError("MCPServerConfig is required")
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client, StdioServerParameters

            server_params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env or None,
            )

            stack = AsyncExitStack()
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))

            # Create session
            self._client = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            self._exit_stack = stack

            # Initialize
            await self._client.initialize()

            # Get available tools
            tools_result = await self._client.list_tools()
            self._tools = self._convert_mcp_tools(tools_result.tools)

            logger.info(f"Connected to MCP server {cfg.name} with {len(self._tools)} tools")

        except Exception as e:
            logger.error(f"Failed to start stdio MCP server: {e}")
            self._tools = []

    async def _connect_http_server(self) -> None:
        """Connect to an MCP server using HTTP transport."""
        cfg = self._mcp_config
        if not cfg:
            raise ValueError("MCPServerConfig is required")
        if not cfg.url:
            raise ValueError("URL is required for HTTP transport")

        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            stack = AsyncExitStack()
            read_stream, write_stream = await stack.enter_async_context(sse_client(cfg.url))

            # Create session
            self._client = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            self._exit_stack = stack

            # Initialize
            await self._client.initialize()

            # Get available tools
            tools_result = await self._client.list_tools()
            self._tools = self._convert_mcp_tools(tools_result.tools)

        except Exception as e:
            logger.error(f"Failed to connect to HTTP MCP server: {e}")
            self._tools = []

    def _convert_mcp_tools(self, mcp_tools: List[Any]) -> List[Tool]:
        """Convert MCP tools to our Tool format."""
        tools = []
        for mcp_tool in mcp_tools:
            mcp_server_name = self._mcp_config.name if self._mcp_config else "unknown"
            tool = Tool(
                name=mcp_tool.name,
                description=mcp_tool.description or "",
                parameters_schema=mcp_tool.inputSchema if hasattr(mcp_tool, 'inputSchema') else {},
                handler=self._create_tool_handler(mcp_tool.name),
                source="mcp",
                metadata={
                    "mcp_server": mcp_server_name,
                },
            )
            tools.append(tool)
        return tools

    def _create_tool_handler(self, tool_name: str):
        """Create a handler function for an MCP tool."""
        async def handler(**kwargs: Any) -> Any:
            if not self._client:
                return {"error": "MCP client not initialized"}

            try:
                result = await self._client.call_tool(tool_name, kwargs)
                return result
            except Exception as e:
                return {"error": str(e)}

        return handler

    async def cleanup(self) -> None:
        """Clean up MCP resources."""
        try:
            if self._exit_stack:
                await self._exit_stack.aclose()
                self._exit_stack = None
            self._client = None

            if self._server_process:
                self._server_process.terminate()
                self._server_process = None

        except Exception as e:
            logger.error(f"Error cleaning up MCP plugin: {e}")

    def get_tools(self) -> List[Tool]:
        """Get all tools from this MCP server."""
        return self._tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call an MCP tool directly.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool result
        """
        if not self._client:
            raise RuntimeError("MCP client not initialized")

        return await self._client.call_tool(tool_name, arguments)

    async def get_resources(self) -> List[Any]:
        """Get available resources from the MCP server."""
        if not self._client:
            return []

        try:
            result = await self._client.list_resources()
            return result.resources
        except Exception as e:
            logger.error(f"Failed to get MCP resources: {e}")
            return []

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the MCP server."""
        if not self._client:
            raise RuntimeError("MCP client not initialized")

        return await self._client.read_resource(uri)
