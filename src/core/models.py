"""
Core data models for Proton agent orchestration platform.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field
from datetime import datetime


class AgentType(str, Enum):
    """Supported agent types."""
    NATIVE = "native"          # Built with agent-framework directly
    COZE = "coze"              # Coze platform agent
    DIFY = "dify"              # Dify platform agent
    DOUBAO = "doubao"          # Doubao (豆包) platform agent
    AUTOGEN = "autogen"        # AutoGen framework agent
    CUSTOM = "custom"          # Custom adapter


class RoutingStrategy(str, Enum):
    """Strategies for routing messages to child agents."""
    SEQUENTIAL = "sequential"          # Execute children one by one
    PARALLEL = "parallel"              # Execute all children in parallel
    CONDITIONAL = "conditional"        # Route based on classifier
    HANDOFF = "handoff"                # Transfer control between agents
    HIERARCHICAL = "hierarchical"      # Decompose and aggregate
    ROUND_ROBIN = "round_robin"        # Distribute evenly
    LOAD_BALANCED = "load_balanced"    # Based on agent load


class ExecutionMode(str, Enum):
    """Workflow execution modes."""
    SYNC = "sync"
    ASYNC = "async"
    STREAMING = "streaming"


class ErrorHandlingStrategy(str, Enum):
    """How to handle errors during execution."""
    FAIL_FAST = "fail_fast"                # Stop on first error
    CONTINUE = "continue"                   # Skip failed agents, continue others
    RETRY = "retry"                         # Retry failed agents
    FALLBACK = "fallback"                   # Use fallback agent


# ============== Plugin Configurations ==============

class MCPServerConfig(BaseModel):
    """Configuration for MCP server connection."""
    name: str
    command: str                            # Command to start MCP server
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    transport: str = "stdio"                # stdio, http, websocket
    url: Optional[str] = None               # For http/websocket transport


class SkillConfig(BaseModel):
    """Configuration for a skill."""
    name: str
    description: str
    module_path: str                        # Python module path
    function_name: str                      # Function name in module
    parameters_schema: Optional[Dict[str, Any]] = None
    approval_required: bool = False


class RAGSourceConfig(BaseModel):
    """Configuration for RAG data source."""
    name: str
    type: str                               # vector_db, file, api
    connection_string: Optional[str] = None
    collection_name: Optional[str] = None
    embedding_model: str = "text-embedding-ada-002"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 5


class PluginConfig(BaseModel):
    """Unified plugin configuration."""
    type: str                               # mcp, skill, rag
    enabled: bool = True
    mcp_config: Optional[MCPServerConfig] = None
    skill_config: Optional[SkillConfig] = None
    rag_config: Optional[RAGSourceConfig] = None


# ============== Agent Configurations ==============

class RetryPolicy(BaseModel):
    """Retry policy for agent execution."""
    max_retries: int = 3
    initial_delay: float = 1.0              # seconds
    max_delay: float = 60.0                 # seconds
    exponential_base: float = 2.0
    retry_on_errors: List[str] = Field(
        default_factory=lambda: ["timeout", "rate_limit", "server_error"]
    )


class NativeAgentConfig(BaseModel):
    """Configuration for native agent-framework agents."""
    instructions: str
    model: str = "gpt-4"
    provider: str = "openai"                # openai, azure, anthropic
    temperature: float = 0.7
    max_tokens: int = 4096
    # Provider-specific settings
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    api_key: Optional[str] = None


class CozeConfig(BaseModel):
    """Configuration for Coze platform agents."""
    bot_id: str
    api_key: str
    api_base: str = "https://api.coze.com"
    conversation_id: Optional[str] = None
    user_id: str = "default_user"


class DifyConfig(BaseModel):
    """Configuration for Dify platform agents."""
    app_id: str
    api_key: str
    api_base: str = "https://api.dify.ai/v1"
    mode: str = "chat"                      # chat, completion, workflow
    user_id: str = "default_user"
    conversation_id: Optional[str] = None


class DoubaoConfig(BaseModel):
    """Configuration for Doubao (豆包) platform agents."""
    bot_id: str
    api_key: str
    api_base: str = "https://api.doubao.com"
    model: str = "doubao-pro"
    user_id: str = "default_user"


class AutoGenConfig(BaseModel):
    """Configuration for AutoGen framework agents."""
    agent_class: str                        # Full class path
    config_list: List[Dict[str, Any]] = Field(default_factory=list)
    system_message: Optional[str] = None
    human_input_mode: str = "NEVER"         # NEVER, ALWAYS, TERMINATE
    max_consecutive_auto_reply: int = 10


class AgentConfig(BaseModel):
    """Unified agent configuration."""
    # Common settings
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 4096

    # Type-specific configurations
    native_config: Optional[NativeAgentConfig] = None
    coze_config: Optional[CozeConfig] = None
    dify_config: Optional[DifyConfig] = None
    doubao_config: Optional[DoubaoConfig] = None
    autogen_config: Optional[AutoGenConfig] = None

    # Plugins
    mcp_servers: List[MCPServerConfig] = Field(default_factory=list)
    skills: List[SkillConfig] = Field(default_factory=list)
    rag_sources: List[RAGSourceConfig] = Field(default_factory=list)


class AgentCapabilities(BaseModel):
    """Describes what an agent can do."""
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_audio: bool = False
    supports_files: bool = False
    max_context_length: int = 128000
    supported_languages: List[str] = Field(default_factory=lambda: ["en", "zh"])


# ============== Message Types ==============

class MessageRole(str, Enum):
    """Message roles."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """A chat message."""
    role: MessageRole
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class ToolCall(BaseModel):
    """A tool call request."""
    id: str
    name: str
    arguments: Dict[str, Any]


class ToolResult(BaseModel):
    """Result from a tool call."""
    tool_call_id: str
    content: str
    is_error: bool = False


class AgentResponse(BaseModel):
    """Response from an agent execution."""
    messages: List[ChatMessage]
    tool_calls: List[ToolCall] = Field(default_factory=list)
    tool_results: List[ToolResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    usage: Optional[Dict[str, int]] = None
    response_id: str = ""


class AgentResponseUpdate(BaseModel):
    """Streaming update from agent execution."""
    delta_content: str = ""
    tool_call: Optional[ToolCall] = None
    is_complete: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============== Workflow Configuration ==============

class WorkflowConfig(BaseModel):
    """Configuration for a workflow."""
    id: str
    name: str
    description: str = ""

    # Tree structure
    root_agent_id: str

    # Global settings
    global_context: Dict[str, Any] = Field(default_factory=dict)
    max_total_depth: int = 10
    total_timeout: float = 300.0

    # Execution settings
    execution_mode: ExecutionMode = ExecutionMode.ASYNC
    error_handling: ErrorHandlingStrategy = ErrorHandlingStrategy.FAIL_FAST

    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    version: str = "1.0.0"
