"""
AutoGen framework agent adapter.

AutoGen (https://github.com/microsoft/autogen) is Microsoft's framework
for building multi-agent conversational systems.
This adapter integrates AutoGen agents into the Proton platform.
"""

import logging
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from .base import AgentAdapter, AdapterFactory
from ..core.models import (
    AgentType,
    AgentResponse,
    AgentResponseUpdate,
    AgentCapabilities,
    ChatMessage,
    MessageRole,
    AutoGenConfig,
)
from ..core.context import ExecutionContext
from ..core.agent_node import AgentNode

logger = logging.getLogger(__name__)


class AutoGenAgentAdapter(AgentAdapter):
    """
    Adapter for AutoGen framework agents.

    Supports:
    - AssistantAgent
    - UserProxyAgent
    - GroupChat (as a single agent)
    - Custom agents
    """

    def __init__(self, node: AgentNode):
        super().__init__(node)
        self._config: Optional[AutoGenConfig] = None
        self._agent = None
        self._autogen_available = False

    async def initialize(self) -> None:
        """Initialize the AutoGen adapter."""
        if self._initialized:
            return

        self._config = self.node.config.autogen_config
        if not self._config:
            raise ValueError(f"AutoGenConfig is required for agent {self.node.id}")

        # Try to import AutoGen
        try:
            import autogen
            self._autogen_available = True
            self._agent = await self._create_autogen_agent()
            logger.info(f"Initialized AutoGen adapter: {self._config.agent_class}")
        except ImportError:
            logger.warning("AutoGen not installed. Using fallback mode.")
            self._autogen_available = False

        self._initialized = True

    async def _create_autogen_agent(self) -> Any:
        """Create the AutoGen agent based on configuration."""
        import autogen

        agent_class_name = self._config.agent_class

        # Build LLM config
        llm_config = {
            "config_list": self._config.config_list,
            "timeout": 120,
        }

        # Create agent based on class type
        if agent_class_name == "AssistantAgent" or agent_class_name == "autogen.AssistantAgent":
            return autogen.AssistantAgent(
                name=self.node.name,
                system_message=self._config.system_message or self.node.description,
                llm_config=llm_config,
            )

        elif agent_class_name == "UserProxyAgent" or agent_class_name == "autogen.UserProxyAgent":
            return autogen.UserProxyAgent(
                name=self.node.name,
                system_message=self._config.system_message,
                human_input_mode=self._config.human_input_mode,
                max_consecutive_auto_reply=self._config.max_consecutive_auto_reply,
                code_execution_config={"use_docker": False},
            )

        elif agent_class_name == "ConversableAgent" or agent_class_name == "autogen.ConversableAgent":
            return autogen.ConversableAgent(
                name=self.node.name,
                system_message=self._config.system_message,
                llm_config=llm_config,
                human_input_mode=self._config.human_input_mode,
            )

        else:
            # Try to dynamically import custom agent class
            try:
                module_path, class_name = agent_class_name.rsplit(".", 1)
                import importlib
                module = importlib.import_module(module_path)
                agent_class = getattr(module, class_name)
                return agent_class(
                    name=self.node.name,
                    system_message=self._config.system_message,
                    llm_config=llm_config,
                )
            except Exception as e:
                logger.error(f"Failed to create custom AutoGen agent: {e}")
                # Fallback to AssistantAgent
                return autogen.AssistantAgent(
                    name=self.node.name,
                    system_message=self._config.system_message,
                    llm_config=llm_config,
                )

    async def run(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AgentResponse:
        """Execute the AutoGen agent."""
        self._ensure_initialized()

        if not self._autogen_available or self._agent is None:
            return self._create_fallback_response(messages)

        try:
            # Get last user message
            user_message = self._get_last_user_message(messages)

            # Create a temporary UserProxyAgent for the conversation
            import autogen
            user_proxy = autogen.UserProxyAgent(
                name="user",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=0,
                code_execution_config=False,
            )

            # Initiate chat
            chat_result = user_proxy.initiate_chat(
                self._agent,
                message=user_message,
                max_turns=1,
            )

            # Extract response
            return self._parse_chat_result(chat_result)

        except Exception as e:
            logger.error(f"AutoGen execution error: {e}")
            return self._create_error_response(str(e))

    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute with streaming (simulated for AutoGen)."""
        self._ensure_initialized()

        # AutoGen doesn't have native streaming support
        # We run the agent and then stream the result
        response = await self.run(messages, context, **kwargs)

        for msg in response.messages:
            # Simulate streaming by yielding chunks
            content = msg.content
            chunk_size = 50

            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                yield AgentResponseUpdate(
                    delta_content=chunk,
                    is_complete=False,
                )

            yield AgentResponseUpdate(
                delta_content="",
                is_complete=True,
            )

    def get_capabilities(self) -> AgentCapabilities:
        """Get AutoGen agent capabilities."""
        return AgentCapabilities(
            supports_streaming=False,  # Simulated streaming
            supports_tools=True,  # AutoGen supports function calling
            supports_vision=False,
            max_context_length=128000,
        )

    def _get_last_user_message(self, messages: List[ChatMessage]) -> str:
        """Extract the last user message."""
        for msg in reversed(messages):
            if msg.role == MessageRole.USER:
                return msg.content
        return ""

    def _parse_chat_result(self, chat_result: Any) -> AgentResponse:
        """Parse AutoGen chat result."""
        messages = []

        # Extract messages from chat history
        if hasattr(chat_result, 'chat_history'):
            for msg in chat_result.chat_history:
                if msg.get('role') == 'assistant' or msg.get('name') == self.node.name:
                    messages.append(ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=msg.get('content', ''),
                        name=self.node.name,
                    ))

        # If no messages extracted, use summary
        if not messages and hasattr(chat_result, 'summary'):
            messages.append(ChatMessage(
                role=MessageRole.ASSISTANT,
                content=chat_result.summary,
                name=self.node.name,
            ))

        # Fallback
        if not messages:
            messages.append(ChatMessage(
                role=MessageRole.ASSISTANT,
                content="[No response from AutoGen agent]",
                name=self.node.name,
            ))

        return AgentResponse(
            messages=messages,
            response_id=str(uuid4()),
            metadata={
                "autogen_cost": getattr(chat_result, 'cost', None),
            },
        )

    def _create_fallback_response(self, messages: List[ChatMessage]) -> AgentResponse:
        """Create a fallback response when AutoGen is not available."""
        user_message = self._get_last_user_message(messages)

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[AutoGen not available. Received: {user_message[:100]}...]",
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={"fallback": True},
        )

    def _create_error_response(self, error: str) -> AgentResponse:
        """Create an error response."""
        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[AutoGen Error: {error}]",
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={"error": error},
        )


# Register the adapter
AdapterFactory.register(AgentType.AUTOGEN, AutoGenAgentAdapter)
