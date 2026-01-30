#!/usr/bin/env python3
"""
Proton Agent Platform - Example Usage

This example demonstrates:
1. Creating a tree-based agent workflow
2. Adding multiple types of agents (native, Coze, Dify)
3. Configuring routing strategies
4. Running the workflow
"""

import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from src.core.models import (
    AgentType,
    AgentConfig,
    NativeAgentConfig,
    CozeConfig,
    DifyConfig,
    RoutingStrategy,
    MCPServerConfig,
    SkillConfig,
)
from src.core.agent_node import AgentNode
from src.orchestration.workflow import Workflow, get_workflow_manager
from src.plugins.registry import get_plugin_registry


async def create_customer_support_workflow():
    """
    Create a customer support workflow with:
    - Triage agent (routes to specialists)
    - Refund specialist
    - Order status specialist
    - Technical support specialist
    """
    manager = get_workflow_manager()

    # Create root triage agent
    triage_agent = AgentNode(
        name="triage_agent",
        description="Routes customer inquiries to the appropriate specialist",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="""You are a customer support triage agent.
                Analyze customer inquiries and route them to the appropriate specialist:
                - For refund requests, route to refund_specialist
                - For order status questions, route to order_specialist
                - For technical issues, route to tech_specialist

                Respond with the specialist name and a brief handoff message.""",
                model="gpt-4",
            )
        ),
        routing_strategy=RoutingStrategy.CONDITIONAL,
    )

    # Create specialist agents
    refund_specialist = AgentNode(
        name="refund_specialist",
        description="Handles refund requests and processes returns",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="""You are a refund specialist. Help customers with:
                - Processing refund requests
                - Explaining refund policies
                - Handling return merchandise authorizations

                Be helpful and empathetic.""",
                model="gpt-4",
            )
        ),
        parent_id=triage_agent.id,
    )

    order_specialist = AgentNode(
        name="order_specialist",
        description="Handles order status inquiries and shipping questions",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="""You are an order status specialist. Help customers with:
                - Checking order status
                - Tracking shipments
                - Updating delivery addresses

                Provide clear and accurate information.""",
                model="gpt-4",
            )
        ),
        parent_id=triage_agent.id,
    )

    tech_specialist = AgentNode(
        name="tech_specialist",
        description="Handles technical support issues",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="""You are a technical support specialist. Help customers with:
                - Troubleshooting product issues
                - Providing setup assistance
                - Explaining features and functionality

                Be patient and provide step-by-step guidance.""",
                model="gpt-4",
            )
        ),
        parent_id=triage_agent.id,
    )

    # Set up routing conditions
    triage_agent.set_routing_condition("refund", refund_specialist.id)
    triage_agent.set_routing_condition("order", order_specialist.id)
    triage_agent.set_routing_condition("technical", tech_specialist.id)
    triage_agent.set_routing_condition("support", tech_specialist.id)

    # Create workflow
    workflow = await manager.create_workflow(
        name="Customer Support",
        description="Multi-agent customer support system",
        root_agent=triage_agent,
    )

    # Add specialist agents
    workflow.add_agent(refund_specialist, triage_agent.id)
    workflow.add_agent(order_specialist, triage_agent.id)
    workflow.add_agent(tech_specialist, triage_agent.id)

    return workflow


async def create_research_workflow():
    """
    Create a research workflow with:
    - Coordinator agent (decomposes research tasks)
    - Web search agent
    - Analysis agent
    - Summary agent
    """
    manager = get_workflow_manager()

    coordinator = AgentNode(
        name="research_coordinator",
        description="Coordinates research tasks and synthesizes findings",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="""You are a research coordinator. Your job is to:
                1. Break down research questions into sub-tasks
                2. Coordinate information gathering
                3. Synthesize findings into coherent answers

                Delegate specific tasks to specialist agents.""",
                model="gpt-4",
            )
        ),
        routing_strategy=RoutingStrategy.HIERARCHICAL,
    )

    searcher = AgentNode(
        name="web_searcher",
        description="Searches for relevant information",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="You search for and gather relevant information on topics.",
                model="gpt-4",
            )
        ),
        parent_id=coordinator.id,
    )

    analyzer = AgentNode(
        name="data_analyzer",
        description="Analyzes and interprets information",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="You analyze data and extract key insights.",
                model="gpt-4",
            )
        ),
        parent_id=coordinator.id,
    )

    # Create workflow
    workflow = await manager.create_workflow(
        name="Research Assistant",
        description="Multi-agent research system",
        root_agent=coordinator,
    )

    workflow.add_agent(searcher, coordinator.id)
    workflow.add_agent(analyzer, coordinator.id)

    return workflow


async def create_hybrid_workflow():
    """
    Create a workflow combining different agent types:
    - Native agent as coordinator
    - Coze agent for specialized tasks
    - Dify agent for workflow automation
    """
    manager = get_workflow_manager()

    # Native coordinator
    coordinator = AgentNode(
        name="hybrid_coordinator",
        description="Coordinates between different agent platforms",
        type=AgentType.NATIVE,
        config=AgentConfig(
            native_config=NativeAgentConfig(
                instructions="You coordinate tasks between different AI platforms.",
                model="gpt-4",
            )
        ),
        routing_strategy=RoutingStrategy.PARALLEL,
    )

    # Coze agent (requires Coze API credentials)
    coze_agent = AgentNode(
        name="coze_specialist",
        description="Coze platform agent for specialized tasks",
        type=AgentType.COZE,
        config=AgentConfig(
            coze_config=CozeConfig(
                bot_id=os.getenv("COZE_BOT_ID", "your_bot_id"),
                api_key=os.getenv("COZE_API_KEY", "your_api_key"),
            )
        ),
        parent_id=coordinator.id,
    )

    # Dify agent (requires Dify API credentials)
    dify_agent = AgentNode(
        name="dify_workflow",
        description="Dify platform agent for workflow automation",
        type=AgentType.DIFY,
        config=AgentConfig(
            dify_config=DifyConfig(
                app_id=os.getenv("DIFY_APP_ID", "your_app_id"),
                api_key=os.getenv("DIFY_API_KEY", "your_api_key"),
                mode="workflow",
            )
        ),
        parent_id=coordinator.id,
    )

    workflow = await manager.create_workflow(
        name="Hybrid Multi-Platform",
        description="Workflow combining multiple AI platforms",
        root_agent=coordinator,
    )

    workflow.add_agent(coze_agent, coordinator.id)
    workflow.add_agent(dify_agent, coordinator.id)

    return workflow


async def main():
    """Run example workflows."""
    print("=" * 60)
    print("Proton Agent Platform - Example")
    print("=" * 60)

    # Create customer support workflow
    print("\n1. Creating Customer Support Workflow...")
    support_workflow = await create_customer_support_workflow()
    print(f"   Created workflow: {support_workflow.name} ({support_workflow.id})")
    print(f"   Agents: {len(support_workflow.tree)}")

    # Initialize and run
    print("\n2. Initializing workflow...")
    await support_workflow.initialize()
    print(f"   State: {support_workflow.state.value}")

    # Example queries
    queries = [
        "I want to return a product I bought last week",
        "Where is my order #12345?",
        "My device won't turn on, can you help?",
    ]

    print("\n3. Running example queries...")
    for query in queries:
        print(f"\n   Query: {query}")
        result = await support_workflow.run(query)
        print(f"   State: {result.state.value}")
        if result.response and result.response.messages:
            response_text = result.response.messages[-1].content[:200]
            print(f"   Response: {response_text}...")
        if result.error:
            print(f"   Error: {result.error}")

    print("\n" + "=" * 60)
    print("Example completed!")


if __name__ == "__main__":
    asyncio.run(main())
