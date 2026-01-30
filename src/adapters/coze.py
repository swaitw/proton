"""
Coze platform agent adapter.

Coze (https://www.coze.com) is a conversational AI platform by ByteDance.
This adapter integrates Coze bots as agents in the Proton platform.
"""

import logging
import aiohttp
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
    CozeConfig,
)
from ..core.context import ExecutionContext
from ..core.agent_node import AgentNode

logger = logging.getLogger(__name__)


class CozeAgentAdapter(AgentAdapter):
    """
    Adapter for Coze platform agents.

    Coze API documentation: https://www.coze.com/docs/developer_guides/coze_api_overview

    Features:
    - Chat with Coze bots
    - Support for streaming responses
    - Conversation management
    """

    def __init__(self, node: AgentNode):
        super().__init__(node)
        self._config: Optional[CozeConfig] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self) -> None:
        """Initialize the Coze adapter."""
        if self._initialized:
            return

        self._config = self.node.config.coze_config
        if not self._config:
            raise ValueError(f"CozeConfig is required for agent {self.node.id}")

        # Create HTTP session
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            }
        )

        self._initialized = True
        logger.info(f"Initialized Coze adapter for bot: {self._config.bot_id}")

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self._session:
            await self._session.close()
            self._session = None

    async def run(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AgentResponse:
        """Execute the Coze bot."""
        self._ensure_initialized()

        # Get last user message
        user_message = self._get_last_user_message(messages)

        # Build request
        url = f"{self._config.api_base}/v3/chat"
        payload = {
            "bot_id": self._config.bot_id,
            "user_id": self._config.user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [
                {
                    "role": "user",
                    "content": user_message,
                    "content_type": "text",
                }
            ],
        }

        if self._config.conversation_id:
            payload["conversation_id"] = self._config.conversation_id

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Coze API error: {response.status} - {error_text}")
                    return self._create_error_response(f"API error: {response.status}")

                data = await response.json()
                return self._parse_response(data)

        except aiohttp.ClientError as e:
            logger.error(f"Coze request failed: {e}")
            return self._create_error_response(str(e))

    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute the Coze bot with streaming."""
        self._ensure_initialized()

        user_message = self._get_last_user_message(messages)

        url = f"{self._config.api_base}/v3/chat"
        payload = {
            "bot_id": self._config.bot_id,
            "user_id": self._config.user_id,
            "stream": True,
            "auto_save_history": True,
            "additional_messages": [
                {
                    "role": "user",
                    "content": user_message,
                    "content_type": "text",
                }
            ],
        }

        if self._config.conversation_id:
            payload["conversation_id"] = self._config.conversation_id

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    yield AgentResponseUpdate(
                        delta_content=f"Error: {response.status}",
                        is_complete=True,
                    )
                    return

                # Parse SSE stream
                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            yield AgentResponseUpdate(
                                delta_content="",
                                is_complete=True,
                            )
                            return

                        import json
                        try:
                            event = json.loads(data)
                            if event.get("event") == "message":
                                msg = event.get("message", {})
                                if msg.get("type") == "answer":
                                    yield AgentResponseUpdate(
                                        delta_content=msg.get("content", ""),
                                        is_complete=False,
                                    )
                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            yield AgentResponseUpdate(
                delta_content=f"Error: {str(e)}",
                is_complete=True,
            )

    def get_capabilities(self) -> AgentCapabilities:
        """Get Coze bot capabilities."""
        return AgentCapabilities(
            supports_streaming=True,
            supports_tools=True,  # Coze bots can have plugins
            supports_vision=True,  # Some bots support images
            supports_files=True,
            max_context_length=32000,
        )

    def _get_last_user_message(self, messages: List[ChatMessage]) -> str:
        """Extract the last user message."""
        for msg in reversed(messages):
            if msg.role == MessageRole.USER:
                return msg.content
        return ""

    def _parse_response(self, data: Dict[str, Any]) -> AgentResponse:
        """Parse Coze API response."""
        messages = []

        # Get messages from response
        response_messages = data.get("messages", [])
        for msg in response_messages:
            if msg.get("type") == "answer":
                messages.append(ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=msg.get("content", ""),
                    name=self.node.name,
                    metadata={
                        "coze_message_id": msg.get("id"),
                        "coze_conversation_id": data.get("conversation_id"),
                    },
                ))

        # If no answer found, create default
        if not messages:
            messages.append(ChatMessage(
                role=MessageRole.ASSISTANT,
                content="[No response from Coze bot]",
                name=self.node.name,
            ))

        return AgentResponse(
            messages=messages,
            response_id=str(uuid4()),
            metadata={
                "coze_conversation_id": data.get("conversation_id"),
                "coze_chat_id": data.get("id"),
            },
        )

    def _create_error_response(self, error: str) -> AgentResponse:
        """Create an error response."""
        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[Coze Error: {error}]",
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={"error": error},
        )


# Register the adapter
AdapterFactory.register(AgentType.COZE, CozeAgentAdapter)
