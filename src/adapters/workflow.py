"""
Adapter for executing workflows as sub-agents.

Allows workflows to reference and call other workflows,
enabling workflow composition and reuse — unlimited nesting depth.

Design:
  - A node with type=WORKFLOW and config.workflow_config.workflow_id
    delegates ALL execution to the referenced workflow.
  - Cycle detection uses workflow_ids tracked in the CallChain.
  - The referenced workflow is lazily initialised on first run so that
    it picks up any agents added after the parent workflow was started.
  - Input / output mapping lets the caller rename context keys across
    workflow boundaries.
"""

import logging
from typing import Any, AsyncIterator, List

from .base import AgentAdapter, AdapterFactory
from ..core.models import (
    AgentType,
    AgentResponse,
    AgentResponseUpdate,
    AgentCapabilities,
    ChatMessage,
    MessageRole,
)
from ..core.context import ExecutionContext, CycleDetectedError
from ..core.agent_node import AgentNode

logger = logging.getLogger(__name__)


class WorkflowAdapter(AgentAdapter):
    """
    Adapter that executes a referenced workflow as a single agent node.

    This is what powers unlimited tree nesting: any AgentNode whose
    type is WORKFLOW will be transparently replaced by the full
    execution of another workflow when the tree executor invokes it.
    """

    def __init__(self, node: AgentNode):
        super().__init__(node)
        self._workflow_manager = None
        # The referenced workflow object (loaded lazily)
        self._referenced_workflow = None

    # ------------------------------------------------------------------
    # AgentAdapter interface
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Validate config and import the workflow manager.

        We do NOT call workflow.initialize() here because the referenced
        workflow may not have all its agents yet (it's lazy).
        """
        config = self.node.config
        if not config or not config.workflow_config:
            raise ValueError(
                f"Node '{self.node.id}' is type WORKFLOW but has no workflow_config."
            )

        workflow_id = config.workflow_config.workflow_id
        if not workflow_id:
            raise ValueError(
                f"Node '{self.node.id}' workflow_config.workflow_id is empty."
            )

        from ..orchestration.workflow import get_workflow_manager
        self._workflow_manager = get_workflow_manager()

        logger.info(
            f"[WorkflowAdapter] Node '{self.node.id}' ({self.node.name}) "
            f"→ sub-workflow '{workflow_id}'"
        )
        self._initialized = True

    async def run(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AgentResponse:
        self._ensure_initialized()

        workflow_id = self.node.config.workflow_config.workflow_id

        # --- Cycle guard ---
        if workflow_id in context.call_chain.get_workflow_ids():
            msg = (
                f"[WorkflowAdapter] Circular workflow reference detected: "
                f"'{workflow_id}' is already in the call chain."
            )
            logger.warning(msg)
            raise CycleDetectedError(msg)

        # --- Resolve input ---
        input_message = self._resolve_input(messages, context)

        # --- Mark workflow in chain ---
        context.call_chain.add_workflow(workflow_id)

        # --- Lazy-load & ensure initialised ---
        workflow = await self._ensure_workflow(workflow_id)
        if not workflow:
            return self._error_response(
                f"Sub-workflow '{workflow_id}' not found.",
                workflow_id,
            )

        # --- Execute ---
        try:
            result = await workflow.run(input_message, context)
        except Exception as exc:
            logger.error(
                f"[WorkflowAdapter] Sub-workflow '{workflow_id}' raised: {exc}"
            )
            return self._error_response(str(exc), workflow_id)

        # --- Build response ---
        if result.error:
            return self._error_response(result.error, workflow_id)

        output_messages = (
            result.response.messages
            if result.response
            else [ChatMessage(
                role=MessageRole.ASSISTANT,
                content="(sub-workflow returned no content)",
            )]
        )

        response = AgentResponse(
            messages=output_messages,
            metadata={
                "sub_workflow_id": workflow_id,
                "execution_id": result.execution_id,
                "duration_ms": result.duration_ms,
            },
        )
        return self._apply_output_mapping(response, context)

    async def run_stream(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
        **kwargs: Any,
    ) -> AsyncIterator[AgentResponseUpdate]:
        self._ensure_initialized()

        workflow_id = self.node.config.workflow_config.workflow_id

        if workflow_id in context.call_chain.get_workflow_ids():
            yield AgentResponseUpdate(
                delta_content=(
                    f"[Error] Circular sub-workflow reference: '{workflow_id}'"
                ),
                is_complete=True,
            )
            return

        input_message = self._resolve_input(messages, context)
        context.call_chain.add_workflow(workflow_id)

        workflow = await self._ensure_workflow(workflow_id)
        if not workflow:
            yield AgentResponseUpdate(
                delta_content=f"[Error] Sub-workflow '{workflow_id}' not found.",
                is_complete=True,
            )
            return

        try:
            async for update in workflow.run_stream(input_message, context):
                yield update
        except Exception as exc:
            yield AgentResponseUpdate(
                delta_content=f"[Sub-workflow Error] {exc}",
                is_complete=True,
            )

    def get_capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_streaming=True,
            supports_tools=True,
            supports_vision=False,
            supports_audio=False,
            supports_files=False,
            max_context_length=128000,
        )

    async def cleanup(self) -> None:
        self._referenced_workflow = None
        self._workflow_manager = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_workflow(self, workflow_id: str):
        """Load and (if needed) initialise the referenced workflow."""
        if (
            self._referenced_workflow is not None
            and self._referenced_workflow.id == workflow_id
        ):
            wf = self._referenced_workflow
        else:
            wf = await self._workflow_manager.get_workflow(workflow_id)
            if wf is None:
                return None
            self._referenced_workflow = wf

        # Ensure the sub-workflow is initialised (idempotent)
        from ..orchestration.workflow import WorkflowState
        if wf.state in (WorkflowState.CREATED,) or wf.executor is None:
            try:
                await wf.initialize()
            except Exception as exc:
                logger.error(
                    f"[WorkflowAdapter] Failed to initialise sub-workflow "
                    f"'{workflow_id}': {exc}"
                )
                return None

        return wf

    def _resolve_input(
        self,
        messages: List[ChatMessage],
        context: ExecutionContext,
    ) -> str:
        """Extract the input string, applying input_mapping if set."""
        base = messages[-1].content if messages else ""
        mapping = (
            self.node.config.workflow_config.input_mapping
            if self.node.config and self.node.config.workflow_config
            else {}
        )
        if not mapping:
            return base

        extra_parts = []
        for target_key, source_key in mapping.items():
            if source_key in context.shared_state:
                extra_parts.append(
                    f"[{target_key}]: {context.shared_state[source_key]}"
                )
        return base + ("\n" + "\n".join(extra_parts) if extra_parts else "")

    def _apply_output_mapping(
        self,
        response: AgentResponse,
        context: ExecutionContext,
    ) -> AgentResponse:
        """Store mapped output keys into shared_state."""
        mapping = (
            self.node.config.workflow_config.output_mapping
            if self.node.config and self.node.config.workflow_config
            else {}
        )
        if not mapping:
            return response
        for target_key, source_key in mapping.items():
            if source_key in response.metadata:
                context.shared_state[target_key] = response.metadata[source_key]
            elif response.messages:
                context.shared_state[target_key] = response.messages[-1].content
        return response

    @staticmethod
    def _error_response(message: str, workflow_id: str) -> AgentResponse:
        return AgentResponse(
            messages=[
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=f"[Sub-workflow Error] {message}",
                )
            ],
            metadata={"error": message, "sub_workflow_id": workflow_id},
        )


# Auto-register when this module is imported
AdapterFactory.register(AgentType.WORKFLOW, WorkflowAdapter)
