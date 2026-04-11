import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.adapters.base import AgentAdapter
from src.core.agent_node import AgentNode, AgentTree
from src.core.context import ExecutionContext
from src.core.models import AgentCapabilities, AgentResponse, AgentResponseUpdate, AgentType, ExecutionEventType, ToolCall
from src.core.tree_executor import TreeExecutor


class FakeStreamingAdapter(AgentAdapter):
    def __init__(self, node: AgentNode, approval_status: str):
        super().__init__(node)
        self.approval_status = approval_status

    async def initialize(self) -> None:
        self._initialized = True

    async def run(self, messages, context, **kwargs):
        _ = messages, context, kwargs
        return AgentResponse(messages=[], response_id="unused")

    async def run_stream(self, messages, context, **kwargs):
        _ = messages, context, kwargs
        yield AgentResponseUpdate(
            tool_call=ToolCall(id="tc-event", name="tool_a", arguments={"x": 1}),
            is_complete=False,
        )
        yield AgentResponseUpdate(
            is_complete=False,
            metadata={
                "tool_result": {
                    "tool_call_id": "tc-event",
                    "content": "approval status",
                    "is_error": self.approval_status == "pending",
                    "metadata": {
                        "approval_status": self.approval_status,
                        "approval_id": "approval-1",
                    },
                }
            },
        )
        yield AgentResponseUpdate(delta_content="done", is_complete=False)
        yield AgentResponseUpdate(is_complete=True)

    def get_capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supports_streaming=True, supports_tools=True)


async def _collect_event_types(approval_status: str):
    root = AgentNode(name="root", type=AgentType.BUILTIN)
    tree = AgentTree()
    tree.add_node(root)

    async def factory(node: AgentNode):
        adapter = FakeStreamingAdapter(node, approval_status)
        await adapter.initialize()
        return adapter

    executor = TreeExecutor(tree=tree, adapter_factory=factory)
    context = ExecutionContext()

    events = []
    async for event in executor.run_stream_with_events(
        input_message="run approval event test",
        workflow_id="wf-events",
        execution_id="exec-events",
        context=context,
    ):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_tree_executor_emits_approval_required_event():
    events = await _collect_event_types("pending")
    event_types = [event.event_type for event in events]

    assert ExecutionEventType.APPROVAL_REQUIRED in event_types
    assert ExecutionEventType.NODE_TOOL_RESULT in event_types

    approval_event = next(
        event for event in events if event.event_type == ExecutionEventType.APPROVAL_REQUIRED
    )
    assert approval_event.metadata["approval_id"] == "approval-1"
    assert approval_event.tool_result is not None
    assert approval_event.tool_result.metadata["approval_status"] == "pending"


@pytest.mark.asyncio
async def test_tree_executor_emits_approval_resolved_event():
    events = await _collect_event_types("approved")
    event_types = [event.event_type for event in events]

    assert ExecutionEventType.APPROVAL_RESOLVED in event_types
    assert ExecutionEventType.NODE_TOOL_RESULT in event_types

    approval_event = next(
        event for event in events if event.event_type == ExecutionEventType.APPROVAL_RESOLVED
    )
    assert approval_event.metadata["approval_id"] == "approval-1"
    assert approval_event.tool_result is not None
    assert approval_event.tool_result.metadata["approval_status"] == "approved"
