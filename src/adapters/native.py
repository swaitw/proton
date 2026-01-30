"""
Native agent adapter using Microsoft Agent Framework directly.

This adapter creates agents using the agent-framework library,
supporting various LLM providers (OpenAI, Azure, Anthropic, etc.)
"""

import logging
from typing import Any, AsyncIterator, List, Optional
from uuid import uuid4

from .base import AgentAdapter, AdapterFactory
from ..core.models import (
    AgentType,
    AgentResponse,
    AgentResponseUpdate,
    AgentCapabilities,
    ChatMessage,
    MessageRole,
    NativeAgentConfig,
)
from ..core.context import ExecutionContext
from ..core.agent_node import AgentNode

logger = logging.getLogger(__name__)


class NativeAgentAdapter(AgentAdapter):
    """
    Adapter for native agents built with Microsoft Agent Framework.

    Supports multiple providers:
    - OpenAI
    - Azure OpenAI
    - Anthropic
    - Ollama
    """

    def __init__(self, node: AgentNode):
        super().__init__(node)
        self._agent = None
        self._chat_client = None
        self._config: Optional[NativeAgentConfig] = None

    async def initialize(self) -> None:
        """Initialize the native agent."""
        if self._initialized:
            return

        self._config = self.node.config.native_config
        if not self._config:
            # Create default config
            self._config = NativeAgentConfig(
                instructions=f"You are {self.node.name}. {self.node.description}",
                model=self.node.config.model,
                temperature=self.node.config.temperature,
            )

        try:
            self._chat_client = await self._create_chat_client()
            self._agent = await self._create_agent()
            self._initialized = True
            logger.info(f"Initialized native agent: {self.node.name}")
        except ImportError as e:
            logger.warning(f"agent-framework not installed: {e}")
            self._initialized = True  # Allow fallback behavior
        except Exception as e:
            logger.error(f"Failed to initialize native agent: {e}")
            raise

    async def _create_chat_client(self) -> Any:
        """Create the chat client based on provider."""
        provider = self._config.provider if self._config else "openai"

        try:
            if provider == "openai":
                from agent_framework.openai import OpenAIChatClient
                return OpenAIChatClient(
                    model=self._config.model if self._config else "gpt-4",
                    api_key=self._config.api_key if self._config else None,
                )

            elif provider == "azure":
                from agent_framework.azure import AzureOpenAIChatClient
                from azure.identity import DefaultAzureCredential

                return AzureOpenAIChatClient(
                    endpoint=self._config.azure_endpoint if self._config else None,
                    deployment=self._config.azure_deployment if self._config else None,
                    credential=DefaultAzureCredential(),
                )

            elif provider == "anthropic":
                from agent_framework.anthropic import AnthropicChatClient
                return AnthropicChatClient(
                    model=self._config.model if self._config else "claude-3-opus-20240229",
                    api_key=self._config.api_key if self._config else None,
                )

            elif provider == "ollama":
                from agent_framework.ollama import OllamaChatClient
                return OllamaChatClient(
                    model=self._config.model if self._config else "llama2",
                )

            else:
                raise ValueError(f"Unknown provider: {provider}")

        except ImportError as e:
            logger.warning(f"Provider {provider} not available: {e}")
            return None

    async def _create_agent(self) -> Any:
        """Create the agent from the chat client."""
        if self._chat_client is None:
            return None

        return self._chat_client.as_agent(
            name=self.node.name,
            instructions=self._config.instructions if self._config else "",
        )

    async def run(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AgentResponse:
        """Execute the agent."""
        self._ensure_initialized()

        if self._agent is None:
            # Fallback: return a simple response
            return self._create_fallback_response(messages)

        try:
            # Convert messages to agent-framework format
            af_messages = self._convert_to_af_messages(messages)

            # Run the agent
            response = await self._agent.run(af_messages)

            # Convert response back
            return self._convert_from_af_response(response)

        except Exception as e:
            logger.error(f"Error running native agent: {e}")
            return AgentResponse(
                messages=[ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=f"Error: {str(e)}",
                    name=self.node.name,
                )],
                response_id=str(uuid4()),
                metadata={"error": str(e)},
            )

    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute the agent with streaming."""
        self._ensure_initialized()

        if self._agent is None:
            yield AgentResponseUpdate(
                delta_content=f"[{self.node.name}]: Agent not available",
                is_complete=True,
            )
            return

        try:
            af_messages = self._convert_to_af_messages(messages)

            async for update in self._agent.run_stream(af_messages):
                yield AgentResponseUpdate(
                    delta_content=update.delta_content if hasattr(update, 'delta_content') else "",
                    is_complete=update.is_complete if hasattr(update, 'is_complete') else False,
                )

        except Exception as e:
            yield AgentResponseUpdate(
                delta_content=f"Error: {str(e)}",
                is_complete=True,
            )

    def get_capabilities(self) -> AgentCapabilities:
        """Get agent capabilities."""
        return AgentCapabilities(
            supports_streaming=True,
            supports_tools=True,
            supports_vision=self._config.model in ["gpt-4-vision-preview", "gpt-4o"] if self._config else False,
            max_context_length=128000 if self._config and "gpt-4" in self._config.model else 16000,
        )

    def _convert_to_af_messages(self, messages: List[ChatMessage]) -> List[Any]:
        """Convert our ChatMessage to agent-framework format."""
        try:
            from agent_framework import ChatMessage as AFChatMessage, Role

            af_messages = []
            for msg in messages:
                role = Role.USER if msg.role == MessageRole.USER else Role.ASSISTANT
                if msg.role == MessageRole.SYSTEM:
                    role = Role.SYSTEM
                af_messages.append(AFChatMessage(
                    role=role,
                    content=msg.content,
                    name=msg.name,
                ))
            return af_messages
        except ImportError:
            return messages

    def _convert_from_af_response(self, response: Any) -> AgentResponse:
        """Convert agent-framework response to our format."""
        messages = []

        if hasattr(response, 'messages'):
            for msg in response.messages:
                messages.append(ChatMessage(
                    role=MessageRole.ASSISTANT if msg.role.value == "assistant" else MessageRole.USER,
                    content=msg.text if hasattr(msg, 'text') else str(msg.content),
                    name=msg.author_name if hasattr(msg, 'author_name') else self.node.name,
                ))
        else:
            messages.append(ChatMessage(
                role=MessageRole.ASSISTANT,
                content=str(response),
                name=self.node.name,
            ))

        return AgentResponse(
            messages=messages,
            response_id=str(uuid4()),
        )

    def _create_fallback_response(self, messages: List[ChatMessage]) -> AgentResponse:
        """Create a fallback response when agent is not available."""
        last_user_message = ""
        for msg in reversed(messages):
            if msg.role == MessageRole.USER:
                last_user_message = msg.content
                break

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[{self.node.name}]: Received message: {last_user_message[:100]}...",
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={"fallback": True},
        )


# Register the adapter
AdapterFactory.register(AgentType.NATIVE, NativeAgentAdapter)
