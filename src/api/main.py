"""
FastAPI application for Proton Agent Platform.

Provides REST API for:
- Agent management
- Workflow orchestration
- Plugin management
- Real-time execution
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

from ..core.models import (
    AgentType,
    AgentConfig,
    RoutingStrategy,
    MCPServerConfig,
    SkillConfig,
    RAGSourceConfig,
)
from ..core.agent_node import AgentNode
from ..orchestration.workflow import (
    Workflow,
    WorkflowManager,
    WorkflowResult,
    WorkflowState,
    get_workflow_manager,
)
from ..plugins.registry import get_plugin_registry

logger = logging.getLogger(__name__)


# ============== Request/Response Models ==============

class CreateAgentRequest(BaseModel):
    """Request to create an agent."""
    name: str
    description: str = ""
    type: AgentType = AgentType.NATIVE
    config: AgentConfig = Field(default_factory=AgentConfig)
    parent_id: Optional[str] = None
    routing_strategy: RoutingStrategy = RoutingStrategy.SEQUENTIAL
    max_depth: int = 5
    timeout: float = 60.0


class UpdateAgentRequest(BaseModel):
    """Request to update an agent."""
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[AgentConfig] = None
    routing_strategy: Optional[RoutingStrategy] = None
    enabled: Optional[bool] = None


class AgentResponse(BaseModel):
    """Agent response model."""
    id: str
    name: str
    description: str
    type: str
    parent_id: Optional[str]
    children: List[str]
    enabled: bool


class CreateWorkflowRequest(BaseModel):
    """Request to create a workflow."""
    name: str
    description: str = ""
    root_agent: Optional[CreateAgentRequest] = None


class RunWorkflowRequest(BaseModel):
    """Request to run a workflow."""
    message: str
    stream: bool = False


class WorkflowResponse(BaseModel):
    """Workflow response model."""
    id: str
    name: str
    description: str
    state: str
    agent_count: int
    created_at: str
    updated_at: str


class ExecutionResponse(BaseModel):
    """Execution result response."""
    workflow_id: str
    execution_id: str
    state: str
    output: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None


class RegisterMCPRequest(BaseModel):
    """Request to register an MCP server."""
    name: str
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    agent_id: Optional[str] = None


class RegisterSkillRequest(BaseModel):
    """Request to register a skill."""
    name: str
    description: str
    module_path: str
    function_name: str
    agent_id: Optional[str] = None


class RegisterRAGRequest(BaseModel):
    """Request to register a RAG source."""
    name: str
    type: str = "vector_db"
    connection_string: Optional[str] = None
    collection_name: Optional[str] = None
    agent_id: Optional[str] = None


# ============== Application ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting Proton Agent Platform...")
    yield
    # Shutdown
    logger.info("Shutting down Proton Agent Platform...")
    plugin_registry = get_plugin_registry()
    await plugin_registry.cleanup_all()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Proton Agent Platform",
        description="Tree-based Agent Orchestration Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ============== Health Check ==============

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "version": "0.1.0"}

    # ============== Workflow Endpoints ==============

    @app.post("/api/workflows", response_model=WorkflowResponse)
    async def create_workflow(request: CreateWorkflowRequest):
        """Create a new workflow."""
        manager = get_workflow_manager()

        root_agent = None
        if request.root_agent:
            root_agent = AgentNode(
                name=request.root_agent.name,
                description=request.root_agent.description,
                type=request.root_agent.type,
                config=request.root_agent.config,
                routing_strategy=request.root_agent.routing_strategy,
                max_depth=request.root_agent.max_depth,
                timeout=request.root_agent.timeout,
            )

        workflow = await manager.create_workflow(
            name=request.name,
            description=request.description,
            root_agent=root_agent,
        )

        return WorkflowResponse(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            state=workflow.state.value,
            agent_count=len(workflow.tree),
            created_at=workflow.created_at.isoformat(),
            updated_at=workflow.updated_at.isoformat(),
        )

    @app.get("/api/workflows", response_model=List[WorkflowResponse])
    async def list_workflows():
        """List all workflows."""
        manager = get_workflow_manager()
        workflows = await manager.list_workflows()

        return [
            WorkflowResponse(
                id=w.id,
                name=w.name,
                description=w.description,
                state=w.state.value,
                agent_count=len(w.tree),
                created_at=w.created_at.isoformat(),
                updated_at=w.updated_at.isoformat(),
            )
            for w in workflows
        ]

    @app.get("/api/workflows/{workflow_id}", response_model=Dict[str, Any])
    async def get_workflow(workflow_id: str):
        """Get workflow details."""
        manager = get_workflow_manager()
        workflow = await manager.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        return workflow.to_dict()

    @app.delete("/api/workflows/{workflow_id}")
    async def delete_workflow(workflow_id: str):
        """Delete a workflow."""
        manager = get_workflow_manager()
        deleted = await manager.delete_workflow(workflow_id)

        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow not found")

        return {"status": "deleted", "id": workflow_id}

    @app.post("/api/workflows/{workflow_id}/run", response_model=ExecutionResponse)
    async def run_workflow(workflow_id: str, request: RunWorkflowRequest):
        """Run a workflow."""
        manager = get_workflow_manager()

        if request.stream:
            # Return streaming response
            async def generate():
                async for update in manager.run_workflow_stream(
                    workflow_id, request.message
                ):
                    yield f"data: {update.delta_content}\n\n"
                    if update.is_complete:
                        yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
            )

        result = await manager.run_workflow(workflow_id, request.message)

        output = None
        if result.response and result.response.messages:
            output = "\n".join(m.content for m in result.response.messages)

        return ExecutionResponse(
            workflow_id=result.workflow_id,
            execution_id=result.execution_id,
            state=result.state.value,
            output=output,
            error=result.error,
            duration_ms=result.duration_ms,
        )

    # ============== Agent Endpoints ==============

    @app.post("/api/workflows/{workflow_id}/agents", response_model=AgentResponse)
    async def add_agent(workflow_id: str, request: CreateAgentRequest):
        """Add an agent to a workflow."""
        manager = get_workflow_manager()
        workflow = await manager.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        node = AgentNode(
            name=request.name,
            description=request.description,
            type=request.type,
            config=request.config,
            parent_id=request.parent_id,
            routing_strategy=request.routing_strategy,
            max_depth=request.max_depth,
            timeout=request.timeout,
        )

        workflow.add_agent(node, request.parent_id)

        return AgentResponse(
            id=node.id,
            name=node.name,
            description=node.description,
            type=node.type.value,
            parent_id=node.parent_id,
            children=node.children,
            enabled=node.enabled,
        )

    @app.get("/api/workflows/{workflow_id}/agents", response_model=List[AgentResponse])
    async def list_agents(workflow_id: str):
        """List all agents in a workflow."""
        manager = get_workflow_manager()
        workflow = await manager.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        return [
            AgentResponse(
                id=node.id,
                name=node.name,
                description=node.description,
                type=node.type.value,
                parent_id=node.parent_id,
                children=node.children,
                enabled=node.enabled,
            )
            for node in workflow.tree
        ]

    @app.delete("/api/workflows/{workflow_id}/agents/{agent_id}")
    async def remove_agent(workflow_id: str, agent_id: str):
        """Remove an agent from a workflow."""
        manager = get_workflow_manager()
        workflow = await manager.get_workflow(workflow_id)

        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        removed = workflow.remove_agent(agent_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Agent not found")

        return {"status": "removed", "id": agent_id}

    # ============== Plugin Endpoints ==============

    @app.post("/api/plugins/mcp")
    async def register_mcp(request: RegisterMCPRequest):
        """Register an MCP server."""
        registry = get_plugin_registry()

        config = MCPServerConfig(
            name=request.name,
            command=request.command,
            args=request.args,
            env=request.env,
        )

        plugin = await registry.register_mcp(config, request.agent_id)

        return {
            "status": "registered",
            "name": request.name,
            "tools": [t.name for t in plugin.get_tools()],
        }

    @app.post("/api/plugins/skill")
    async def register_skill(request: RegisterSkillRequest):
        """Register a skill."""
        registry = get_plugin_registry()

        config = SkillConfig(
            name=request.name,
            description=request.description,
            module_path=request.module_path,
            function_name=request.function_name,
        )

        plugin = await registry.register_skill(config, request.agent_id)

        return {
            "status": "registered",
            "name": request.name,
            "tools": [t.name for t in plugin.get_tools()],
        }

    @app.post("/api/plugins/rag")
    async def register_rag(request: RegisterRAGRequest):
        """Register a RAG source."""
        registry = get_plugin_registry()

        config = RAGSourceConfig(
            name=request.name,
            type=request.type,
            connection_string=request.connection_string,
            collection_name=request.collection_name,
        )

        plugin = await registry.register_rag(config, request.agent_id)

        return {
            "status": "registered",
            "name": request.name,
            "tools": [t.name for t in plugin.get_tools()],
        }

    @app.get("/api/plugins")
    async def list_plugins():
        """List all registered plugins."""
        registry = get_plugin_registry()
        plugins = registry.get_all_plugins()

        return [
            {
                "id": plugin_id,
                "type": plugin.plugin_type,
                "enabled": plugin.is_enabled,
                "tools": [t.name for t in plugin.get_tools()],
            }
            for plugin_id, plugin in plugins.items()
        ]

    @app.delete("/api/plugins/{plugin_id}")
    async def remove_plugin(plugin_id: str):
        """Remove a plugin."""
        registry = get_plugin_registry()
        plugin = await registry.remove_plugin(plugin_id)

        if not plugin:
            raise HTTPException(status_code=404, detail="Plugin not found")

        return {"status": "removed", "id": plugin_id}

    return app


# Create default app instance
app = create_app()


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the API server."""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
