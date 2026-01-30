"""
Doubao (豆包) platform agent adapter.

Doubao is ByteDance's AI assistant platform.
This adapter integrates Doubao bots as agents in the Proton platform.
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
    DoubaoConfig,
)
from ..core.context import ExecutionContext
from ..core.agent_node import AgentNode

logger = logging.getLogger(__name__)


class DoubaoAgentAdapter(AgentAdapter):
    """
    Adapter for Doubao (豆包) platform agents.

    Doubao API: https://www.volcengine.com/docs/82379

    Features:
    - Chat with Doubao models
    - Support for streaming responses
    - Multiple model variants
    """

    def __init__(self, node: AgentNode):
        super().__init__(node)
        self._config: Optional[DoubaoConfig] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self) -> None:
        """Initialize the Doubao adapter."""
        if self._initialized:
            return

        self._config = self.node.config.doubao_config
        if not self._config:
            raise ValueError(f"DoubaoConfig is required for agent {self.node.id}")

        # Create HTTP session with Volcengine auth
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            }
        )

        self._initialized = True
        logger.info(f"Initialized Doubao adapter with model: {self._config.model}")

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
        """Execute the Doubao model."""
        self._ensure_initialized()

        # Convert messages to Doubao format
        doubao_messages = self._convert_messages(messages)

        # Use Volcengine Ark API endpoint
        url = f"{self._config.api_base}/api/v3/chat/completions"
        payload = {
            "model": self._config.model,
            "messages": doubao_messages,
            "stream": False,
        }

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Doubao API error: {response.status} - {error_text}")
                    return self._create_error_response(f"API error: {response.status}")

                data = await response.json()
                return self._parse_response(data)

        except aiohttp.ClientError as e:
            logger.error(f"Doubao request failed: {e}")
            return self._create_error_response(str(e))

    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute with streaming."""
        self._ensure_initialized()

        doubao_messages = self._convert_messages(messages)

        url = f"{self._config.api_base}/api/v3/chat/completions"
        payload = {
            "model": self._config.model,
            "messages": doubao_messages,
            "stream": True,
        }

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
                            chunk = json.loads(data)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield AgentResponseUpdate(
                                        delta_content=content,
                                        is_complete=False,
                                    )

                                # Check for finish
                                if choices[0].get("finish_reason"):
                                    yield AgentResponseUpdate(
                                        delta_content="",
                                        is_complete=True,
                                    )
                                    return

                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            yield AgentResponseUpdate(
                delta_content=f"Error: {str(e)}",
                is_complete=True,
            )

    def get_capabilities(self) -> AgentCapabilities:
        """Get Doubao capabilities."""
        return AgentCapabilities(
            supports_streaming=True,
            supports_tools=True,
            supports_vision="vision" in self._config.model if self._config else False,
            max_context_length=32000,
            supported_languages=["zh", "en"],
        )

    def _convert_messages(self, messages: List[ChatMessage]) -> List[Dict[str, str]]:
        """Convert messages to Doubao format."""
        doubao_messages = []
        for msg in messages:
            role = msg.role.value
            if role == "tool":
                role = "assistant"  # Map tool to assistant
            doubao_messages.append({
                "role": role,
                "content": msg.content,
            })
        return doubao_messages

    def _parse_response(self, data: Dict[str, Any]) -> AgentResponse:
        """Parse Doubao API response."""
        choices = data.get("choices", [])

        if not choices:
            return self._create_error_response("No response choices")

        message = choices[0].get("message", {})
        content = message.get("content", "")

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=content,
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={
                "model": data.get("model"),
                "usage": data.get("usage", {}),
            },
            usage=data.get("usage"),
        )

    def _create_error_response(self, error: str) -> AgentResponse:
        """Create an error response."""
        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[Doubao Error: {error}]",
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={"error": error},
        )


# Register the adapter
AdapterFactory.register(AgentType.DOUBAO, DoubaoAgentAdapter)
