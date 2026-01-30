"""
Workflow management for orchestrating agent trees.

Provides:
- Workflow lifecycle management
- State persistence
- Execution history
"""

import logging
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from ..core.models import (
    AgentResponse,
    AgentResponseUpdate,
    ChatMessage,
    MessageRole,
    WorkflowConfig,
)
from ..core.agent_node import AgentNode, AgentTree
from ..core.context import ExecutionContext
from ..core.tree_executor import TreeExecutor, WorkflowBuilder
from ..adapters.base import AdapterFactory, create_adapter_for_node
from ..plugins.registry import PluginRegistry, get_plugin_registry

logger = logging.getLogger(__name__)


class WorkflowState(str, Enum):
    """Workflow execution states."""
    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowResult:
    """Result of a workflow execution."""
    workflow_id: str
    execution_id: str
    state: WorkflowState
    response: Optional[AgentResponse] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    """
    A workflow represents a configured agent tree ready for execution.

    Workflows can be:
    - Created from configuration
    - Persisted and reloaded
    - Executed multiple times
    - Monitored and managed
    """
    id: str
    name: str
    description: str
    config: WorkflowConfig
    tree: AgentTree
    state: WorkflowState = WorkflowState.CREATED

    # Runtime
    executor: Optional[TreeExecutor] = field(default=None, repr=False)
    plugin_registry: Optional[PluginRegistry] = field(default=None, repr=False)

    # History
    executions: List[WorkflowResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_config(cls, config: WorkflowConfig) -> "Workflow":
        """Create a workflow from configuration."""
        tree = AgentTree()
        tree.root_id = config.root_agent_id

        return cls(
            id=config.id,
            name=config.name,
            description=config.description,
            config=config,
            tree=tree,
        )

    async def initialize(self) -> None:
        """Initialize the workflow for execution."""
        if self.state not in (WorkflowState.CREATED, WorkflowState.READY):
            raise RuntimeError(f"Cannot initialize workflow in state: {self.state}")

        self.state = WorkflowState.INITIALIZING

        try:
            # Initialize plugin registry
            self.plugin_registry = get_plugin_registry()

            # Initialize plugins for each node
            for node in self.tree:
                await self.plugin_registry.initialize_for_node(node)

            # Create executor
            self.executor = TreeExecutor(
                tree=self.tree,
                adapter_factory=create_adapter_for_node,
            )

            await self.executor.initialize()

            self.state = WorkflowState.READY
            self.updated_at = datetime.now()
            logger.info(f"Workflow {self.id} initialized successfully")

        except Exception as e:
            self.state = WorkflowState.FAILED
            logger.error(f"Failed to initialize workflow {self.id}: {e}")
            raise

    async def run(
        self,
        input_message: str,
        context: Optional[ExecutionContext] = None,
    ) -> WorkflowResult:
        """
        Execute the workflow with the given input.

        Args:
            input_message: User's input message
            context: Optional execution context

        Returns:
            WorkflowResult with execution details
        """
        if self.state != WorkflowState.READY:
            if self.state == WorkflowState.CREATED:
                await self.initialize()
            else:
                raise RuntimeError(f"Workflow not ready, state: {self.state}")

        execution_id = str(uuid4())
        started_at = datetime.now()

        self.state = WorkflowState.RUNNING

        try:
            response = await self.executor.run(input_message, context)

            completed_at = datetime.now()
            duration_ms = (completed_at - started_at).total_seconds() * 1000

            result = WorkflowResult(
                workflow_id=self.id,
                execution_id=execution_id,
                state=WorkflowState.COMPLETED,
                response=response,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )

            self.executions.append(result)
            self.state = WorkflowState.READY
            self.updated_at = datetime.now()

            return result

        except Exception as e:
            completed_at = datetime.now()
            duration_ms = (completed_at - started_at).total_seconds() * 1000

            result = WorkflowResult(
                workflow_id=self.id,
                execution_id=execution_id,
                state=WorkflowState.FAILED,
                error=str(e),
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )

            self.executions.append(result)
            self.state = WorkflowState.READY  # Ready for retry
            self.updated_at = datetime.now()

            logger.error(f"Workflow {self.id} execution failed: {e}")
            return result

    async def run_stream(
        self,
        input_message: str,
        context: Optional[ExecutionContext] = None,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute the workflow with streaming output."""
        if self.state != WorkflowState.READY:
            if self.state == WorkflowState.CREATED:
                await self.initialize()
            else:
                yield AgentResponseUpdate(
                    delta_content=f"Workflow not ready: {self.state}",
                    is_complete=True,
                )
                return

        self.state = WorkflowState.RUNNING

        try:
            async for update in self.executor.run_stream(input_message, context):
                yield update
        except Exception as e:
            yield AgentResponseUpdate(
                delta_content=f"Error: {e}",
                is_complete=True,
            )
        finally:
            self.state = WorkflowState.READY

    def add_agent(self, node: AgentNode, parent_id: Optional[str] = None) -> None:
        """Add an agent to the workflow."""
        if parent_id:
            node.parent_id = parent_id
        self.tree.add_node(node)
        self.updated_at = datetime.now()

    def remove_agent(self, node_id: str) -> Optional[AgentNode]:
        """Remove an agent from the workflow."""
        node = self.tree.remove_node(node_id)
        if node:
            self.updated_at = datetime.now()
        return node

    def get_agent(self, node_id: str) -> Optional[AgentNode]:
        """Get an agent by ID."""
        return self.tree.get_node(node_id)

    def to_dict(self) -> Dict[str, Any]:
        """Convert workflow to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "config": self.config.model_dump(),
            "tree": self.tree.to_dict(),
            "state": self.state.value,
            "executions": [
                {
                    "execution_id": e.execution_id,
                    "state": e.state.value,
                    "started_at": e.started_at.isoformat() if e.started_at else None,
                    "completed_at": e.completed_at.isoformat() if e.completed_at else None,
                    "duration_ms": e.duration_ms,
                    "error": e.error,
                }
                for e in self.executions[-10:]  # Keep last 10
            ],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class WorkflowManager:
    """
    Manages multiple workflows.

    Provides:
    - CRUD operations for workflows
    - Workflow execution
    - State persistence
    """

    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}
        self._lock = asyncio.Lock()

    async def create_workflow(
        self,
        name: str,
        description: str = "",
        root_agent: Optional[AgentNode] = None,
    ) -> Workflow:
        """
        Create a new workflow.

        Args:
            name: Workflow name
            description: Workflow description
            root_agent: Optional root agent node

        Returns:
            Created Workflow
        """
        workflow_id = str(uuid4())

        config = WorkflowConfig(
            id=workflow_id,
            name=name,
            description=description,
            root_agent_id=root_agent.id if root_agent else "",
        )

        workflow = Workflow.from_config(config)

        if root_agent:
            workflow.add_agent(root_agent)

        async with self._lock:
            self._workflows[workflow_id] = workflow

        logger.info(f"Created workflow: {workflow_id}")
        return workflow

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Get a workflow by ID."""
        return self._workflows.get(workflow_id)

    async def list_workflows(self) -> List[Workflow]:
        """List all workflows."""
        return list(self._workflows.values())

    async def update_workflow(
        self,
        workflow_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Workflow]:
        """Update a workflow."""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return None

        if "name" in updates:
            workflow.name = updates["name"]
        if "description" in updates:
            workflow.description = updates["description"]

        workflow.updated_at = datetime.now()
        return workflow

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow."""
        async with self._lock:
            if workflow_id in self._workflows:
                del self._workflows[workflow_id]
                logger.info(f"Deleted workflow: {workflow_id}")
                return True
        return False

    async def run_workflow(
        self,
        workflow_id: str,
        input_message: str,
    ) -> WorkflowResult:
        """Execute a workflow."""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return WorkflowResult(
                workflow_id=workflow_id,
                execution_id="",
                state=WorkflowState.FAILED,
                error="Workflow not found",
            )

        return await workflow.run(input_message)

    async def run_workflow_stream(
        self,
        workflow_id: str,
        input_message: str,
    ) -> AsyncIterator[AgentResponseUpdate]:
        """Execute a workflow with streaming."""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            yield AgentResponseUpdate(
                delta_content="Workflow not found",
                is_complete=True,
            )
            return

        async for update in workflow.run_stream(input_message):
            yield update


# Global workflow manager instance
_global_manager: Optional[WorkflowManager] = None


def get_workflow_manager() -> WorkflowManager:
    """Get the global workflow manager."""
    global _global_manager
    if _global_manager is None:
        _global_manager = WorkflowManager()
    return _global_manager
