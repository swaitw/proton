"""
Dify platform agent adapter.

Dify (https://dify.ai) is an open-source LLM application development platform.
This adapter integrates Dify apps as agents in the Proton platform.
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
    DifyConfig,
)
from ..core.context import ExecutionContext
from ..core.agent_node import AgentNode

logger = logging.getLogger(__name__)


class DifyAgentAdapter(AgentAdapter):
    """
    Adapter for Dify platform applications.

    Dify API documentation: https://docs.dify.ai/api-reference

    Supports:
    - Chat applications
    - Completion applications
    - Workflow applications
    """

    def __init__(self, node: AgentNode):
        super().__init__(node)
        self._config: Optional[DifyConfig] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self) -> None:
        """Initialize the Dify adapter."""
        if self._initialized:
            return

        self._config = self.node.config.dify_config
        if not self._config:
            raise ValueError(f"DifyConfig is required for agent {self.node.id}")

        # Create HTTP session
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            }
        )

        self._initialized = True
        logger.info(f"Initialized Dify adapter for app: {self._config.app_id}")

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
        """Execute the Dify app."""
        self._ensure_initialized()

        if self._config.mode == "chat":
            return await self._run_chat(messages, context)
        elif self._config.mode == "completion":
            return await self._run_completion(messages, context)
        elif self._config.mode == "workflow":
            return await self._run_workflow(messages, context)
        else:
            return self._create_error_response(f"Unknown mode: {self._config.mode}")

    async def _run_chat(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
    ) -> AgentResponse:
        """Run in chat mode."""
        user_message = self._get_last_user_message(messages)

        url = f"{self._config.api_base}/chat-messages"
        payload = {
            "inputs": {},
            "query": user_message,
            "response_mode": "blocking",
            "user": self._config.user_id,
        }

        if self._config.conversation_id:
            payload["conversation_id"] = self._config.conversation_id

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Dify API error: {response.status} - {error_text}")
                    return self._create_error_response(f"API error: {response.status}")

                data = await response.json()
                return self._parse_chat_response(data)

        except aiohttp.ClientError as e:
            logger.error(f"Dify request failed: {e}")
            return self._create_error_response(str(e))

    async def _run_completion(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
    ) -> AgentResponse:
        """Run in completion mode."""
        # Combine messages into a single prompt
        prompt = "\n".join(
            f"{msg.role.value}: {msg.content}"
            for msg in messages
        )

        url = f"{self._config.api_base}/completion-messages"
        payload = {
            "inputs": {"prompt": prompt},
            "response_mode": "blocking",
            "user": self._config.user_id,
        }

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return self._create_error_response(f"API error: {response.status}")

                data = await response.json()
                return self._parse_completion_response(data)

        except aiohttp.ClientError as e:
            return self._create_error_response(str(e))

    async def _run_workflow(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
    ) -> AgentResponse:
        """Run in workflow mode."""
        user_message = self._get_last_user_message(messages)

        url = f"{self._config.api_base}/workflows/run"
        payload = {
            "inputs": {"query": user_message},
            "response_mode": "blocking",
            "user": self._config.user_id,
        }

        try:
            async with self._session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return self._create_error_response(f"API error: {response.status}")

                data = await response.json()
                return self._parse_workflow_response(data)

        except aiohttp.ClientError as e:
            return self._create_error_response(str(e))

    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute with streaming."""
        self._ensure_initialized()

        user_message = self._get_last_user_message(messages)

        if self._config.mode == "chat":
            url = f"{self._config.api_base}/chat-messages"
            payload = {
                "inputs": {},
                "query": user_message,
                "response_mode": "streaming",
                "user": self._config.user_id,
            }
        elif self._config.mode == "workflow":
            url = f"{self._config.api_base}/workflows/run"
            payload = {
                "inputs": {"query": user_message},
                "response_mode": "streaming",
                "user": self._config.user_id,
            }
        else:
            yield AgentResponseUpdate(
                delta_content=f"Streaming not supported for mode: {self._config.mode}",
                is_complete=True,
            )
            return

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
                            event_type = event.get("event")

                            if event_type == "message":
                                yield AgentResponseUpdate(
                                    delta_content=event.get("answer", ""),
                                    is_complete=False,
                                )
                            elif event_type == "message_end":
                                yield AgentResponseUpdate(
                                    delta_content="",
                                    is_complete=True,
                                    metadata=event.get("metadata", {}),
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
        """Get Dify app capabilities."""
        return AgentCapabilities(
            supports_streaming=True,
            supports_tools=True,
            supports_vision=True,
            supports_files=True,
            max_context_length=128000,  # Depends on model
        )

    def _get_last_user_message(self, messages: List[ChatMessage]) -> str:
        """Extract the last user message."""
        for msg in reversed(messages):
            if msg.role == MessageRole.USER:
                return msg.content
        return ""

    def _parse_chat_response(self, data: Dict[str, Any]) -> AgentResponse:
        """Parse chat mode response."""
        answer = data.get("answer", "")

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=answer,
                name=self.node.name,
                metadata={
                    "dify_message_id": data.get("message_id"),
                    "dify_conversation_id": data.get("conversation_id"),
                },
            )],
            response_id=str(uuid4()),
            metadata={
                "dify_conversation_id": data.get("conversation_id"),
                "token_usage": data.get("metadata", {}).get("usage", {}),
            },
        )

    def _parse_completion_response(self, data: Dict[str, Any]) -> AgentResponse:
        """Parse completion mode response."""
        answer = data.get("answer", "")

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=answer,
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={
                "dify_message_id": data.get("message_id"),
            },
        )

    def _parse_workflow_response(self, data: Dict[str, Any]) -> AgentResponse:
        """Parse workflow mode response."""
        outputs = data.get("data", {}).get("outputs", {})

        # Try to get the main output
        answer = outputs.get("text", outputs.get("output", str(outputs)))

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=answer,
                name=self.node.name,
                metadata={"workflow_outputs": outputs},
            )],
            response_id=str(uuid4()),
            metadata={
                "dify_workflow_run_id": data.get("workflow_run_id"),
                "dify_task_id": data.get("task_id"),
            },
        )

    def _create_error_response(self, error: str) -> AgentResponse:
        """Create an error response."""
        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[Dify Error: {error}]",
                name=self.node.name,
            )],
            response_id=str(uuid4()),
            metadata={"error": error},
        )


# Register the adapter
AdapterFactory.register(AgentType.DIFY, DifyAgentAdapter)
