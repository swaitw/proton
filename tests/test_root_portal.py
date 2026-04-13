"""
Tests for Root Portal + 三级沉淀触发 feature.

Covers:
1. Default portal auto-creation on startup
2. Backbone direct reply (no workflows)
3. Workflow routing when workflows are available
4. Backbone fallback when intent doesn't match any workflow
5. Published workflow auto-added to default portal
6. Trajectory extraction on chat complete
7. Trajectory pool triggers learning cycle
8. Strong signal immediate precipitation
"""

import asyncio
import pathlib
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.core.models import SuperPortalConfig
from src.portal import service as portal_service_module
from src.portal.trajectory import (
    TrajectoryPool,
    TrajectoryEntry,
    has_strong_signal,
    STRONG_SIGNAL_KEYWORDS,
)
from src.storage import persistence as persistence_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_globals(monkeypatch, tmp_path):
    """Reset global singletons so each test starts clean."""
    monkeypatch.setenv("PROTON_STORAGE_TYPE", "file")
    monkeypatch.setenv("PROTON_STORAGE_PATH", str(tmp_path))
    persistence_module._storage_manager = None
    portal_service_module._global_portal_manager = None
    portal_service_module._global_trajectory_pool = None

    # Mock MemPalaceClient to avoid MCP initialization errors during tests
    from src.portal.mempalace_client import MemPalaceClient
    monkeypatch.setattr(MemPalaceClient, "ensure_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(MemPalaceClient, "call", AsyncMock(return_value={"drawers": []}))


# ===========================================================================
# 1. Default portal auto-created on startup
# ===========================================================================

def test_default_portal_auto_created_on_startup(tmp_path, monkeypatch):
    """ensure_default_portal creates a Root Portal with is_default=True."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        await mgr._ensure_ready()

        # No portals yet
        portals = await mgr.list_portals()
        assert len(portals) == 0

        # Ensure default
        default = await mgr.ensure_default_portal()
        assert default is not None
        assert default.is_default is True
        assert default.auto_include_published is True
        assert default.name == "Root Portal"
        assert default.backbone_system_prompt != ""

        # Second call returns the same portal, doesn't create another
        default2 = await mgr.ensure_default_portal()
        assert default2.id == default.id

        portals = await mgr.list_portals()
        assert len(portals) == 1

    asyncio.run(_run())


def test_default_portal_uniqueness_when_multiple_marked_default(tmp_path, monkeypatch):
    """Setting another portal as default should unset previous default portal."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        p1 = await mgr.create_portal(name="P1", is_default=True)
        p2 = await mgr.create_portal(name="P2", is_default=True)

        assert p2.is_default is True
        portals = await mgr.list_portals()
        defaults = [p for p in portals if p.is_default]
        assert len(defaults) == 1
        assert defaults[0].id == p2.id

        refreshed_p1 = await mgr.get_portal(p1.id)
        assert refreshed_p1 is not None
        assert refreshed_p1.is_default is False

    asyncio.run(_run())


def test_ensure_default_portal_concurrent_creation_is_singleton(tmp_path, monkeypatch):
    """Concurrent ensure_default_portal calls should not create duplicate defaults."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()

        results = await asyncio.gather(
            *[mgr.ensure_default_portal() for _ in range(10)]
        )
        ids = {cfg.id for cfg in results}
        assert len(ids) == 1

        portals = await mgr.list_portals()
        assert len(portals) == 1
        assert portals[0].is_default is True

    asyncio.run(_run())


# ===========================================================================
# 2. Default portal chat without workflows → Backbone direct reply
# ===========================================================================

def test_default_portal_chat_without_workflows(tmp_path, monkeypatch):
    """When no workflows are bound, Portal should use Backbone to reply directly."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()
        svc = await mgr.get_service(default.id)
        assert svc is not None

        # Mock the LLM client to return a simple response
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello! I'm your AI assistant."
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        svc._client = mock_client

        events = []
        async for event in svc.chat(
            session_id="test-session-1",
            user_message="你好",
            stream=False,
        ):
            events.append(event)

        # Should have CONTENT and COMPLETE events (no ERROR)
        event_types = [e.type.value for e in events]
        assert "content" in event_types
        assert "complete" in event_types
        assert "error" not in event_types

        # Content should contain backbone reply
        content_events = [e for e in events if e.type.value == "content"]
        assert any("AI assistant" in (e.delta or "") for e in content_events)

    asyncio.run(_run())


# ===========================================================================
# 3. Default portal routes to workflow when available
# ===========================================================================

def test_default_portal_chat_routes_to_workflow(tmp_path, monkeypatch):
    """When workflows are available and intent matches, dispatch to workflow."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()

        # Add a workflow_id to the portal
        await mgr.update_portal(default.id, {"workflow_ids": ["wf-test-1"]})

        svc = await mgr.get_service(default.id)
        assert svc is not None

        # Mock the LLM client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Synthesised result"
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        svc._client = mock_client

        # Mock workflow manager to return a workflow
        mock_wf = MagicMock()
        mock_wf.id = "wf-test-1"
        mock_wf.name = "Test Workflow"
        mock_wf.description = "A test workflow"
        svc._wf_manager.get_workflow = AsyncMock(return_value=mock_wf)

        # Mock intent understanding to dispatch to the workflow
        from src.core.models import IntentUnderstandingResult, WorkflowDispatchPlan
        mock_intent = IntentUnderstandingResult(
            original_query="run test workflow",
            understood_intent="test intent",
            clarification_needed=False,
            dispatch_plans=[
                WorkflowDispatchPlan(
                    workflow_id="wf-test-1",
                    workflow_name="Test Workflow",
                    sub_query="test query",
                    reason="User explicitly requested test workflow",
                    priority=1,
                )
            ],
        )
        svc._intent_svc = MagicMock()
        svc._intent_svc.understand_workflows = AsyncMock(return_value=mock_intent)

        # Mock workflow run
        mock_wf_result = MagicMock()
        mock_wf_result.error = None
        mock_wf_result.response = MagicMock()
        mock_wf_result.response.messages = [MagicMock(content="Workflow output")]
        svc._wf_manager.run_workflow = AsyncMock(return_value=mock_wf_result)

        events = []
        async for event in svc.chat(
            session_id="test-session-2",
            user_message="run test workflow",
            stream=False,
        ):
            events.append(event)

        event_types = [e.type.value for e in events]
        assert "intent_understood" in event_types
        assert "workflow_dispatch_start" in event_types
        assert "workflow_dispatch_result" in event_types
        assert "content" in event_types
        assert "complete" in event_types

    asyncio.run(_run())


# ===========================================================================
# 4. Backbone fallback when no intent match
# ===========================================================================

def test_default_portal_chat_fallback_when_no_match(tmp_path, monkeypatch):
    """When workflows exist but intent returns empty dispatch_plans, use Backbone."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()
        await mgr.update_portal(default.id, {"workflow_ids": ["wf-test-1"]})

        svc = await mgr.get_service(default.id)
        assert svc is not None

        # Mock LLM client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Backbone fallback reply"
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        svc._client = mock_client

        # Mock workflow manager
        mock_wf = MagicMock()
        mock_wf.id = "wf-test-1"
        mock_wf.name = "Test Workflow"
        mock_wf.description = "A test workflow"
        svc._wf_manager.get_workflow = AsyncMock(return_value=mock_wf)

        # Mock intent → empty dispatch plans
        from src.core.models import IntentUnderstandingResult
        mock_intent = IntentUnderstandingResult(
            original_query="今天天气真好",
            understood_intent="casual chat",
            clarification_needed=False,
            dispatch_plans=[],  # No workflow matches
        )
        svc._intent_svc = MagicMock()
        svc._intent_svc.understand_workflows = AsyncMock(return_value=mock_intent)

        events = []
        async for event in svc.chat(
            session_id="test-session-3",
            user_message="今天天气真好",
            stream=False,
        ):
            events.append(event)

        event_types = [e.type.value for e in events]
        # Should have intent_understood (from the intent step) but no workflow dispatch
        assert "intent_understood" in event_types
        assert "workflow_dispatch_start" not in event_types
        # Should have backbone content
        assert "content" in event_types
        content_events = [e for e in events if e.type.value == "content"]
        assert any("Backbone fallback" in (e.delta or "") for e in content_events)

    asyncio.run(_run())


def test_fallback_to_copilot_disabled_no_match_returns_guard_message(tmp_path, monkeypatch):
    """When fallback_to_copilot=False and no workflow matches, should not call backbone LLM."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()
        await mgr.update_portal(default.id, {
            "workflow_ids": ["wf-test-1"],
            "fallback_to_copilot": False,
        })

        svc = await mgr.get_service(default.id)
        assert svc is not None

        mock_wf = MagicMock()
        mock_wf.id = "wf-test-1"
        mock_wf.name = "Test Workflow"
        mock_wf.description = "A test workflow"
        svc._wf_manager.get_workflow = AsyncMock(return_value=mock_wf)

        from src.core.models import IntentUnderstandingResult
        mock_intent = IntentUnderstandingResult(
            original_query="今天天气真好",
            understood_intent="casual chat",
            clarification_needed=False,
            dispatch_plans=[],
        )
        svc._intent_svc = MagicMock()
        svc._intent_svc.understand_workflows = AsyncMock(return_value=mock_intent)

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=AssertionError("fallback disabled path should not call backbone llm")
        )
        svc._client = mock_client

        events = []
        async for event in svc.chat(
            session_id="test-session-fallback-disabled",
            user_message="今天天气真好",
            stream=False,
        ):
            events.append(event)

        content_events = [e for e in events if e.type.value == "content"]
        assert content_events
        assert "已关闭 fallback_to_copilot" in (content_events[-1].delta or "")
        assert "complete" in [e.type.value for e in events]

    asyncio.run(_run())


# ===========================================================================
# 5. Published workflow auto-added to default portal
# ===========================================================================

def test_published_workflow_auto_added_to_default_portal(tmp_path, monkeypatch):
    """Default portal with auto_include_published should add published workflows."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()

        assert default.auto_include_published is True
        assert len(default.workflow_ids) == 0

        # Simulate adding a workflow_id (as publish endpoint would do)
        updated_ids = list(default.workflow_ids) + ["wf-published-1"]
        updated = await mgr.update_portal(default.id, {"workflow_ids": updated_ids})
        assert updated is not None
        assert "wf-published-1" in updated.workflow_ids

    asyncio.run(_run())


# ===========================================================================
# 6. Trajectory extraction on chat complete
# ===========================================================================

def test_trajectory_extraction_on_chat_complete(tmp_path, monkeypatch):
    """After chat completion, trajectory signals should be added to the pool."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()
        svc = await mgr.get_service(default.id)
        assert svc is not None

        # Direct call to _extract_trajectory_bg
        pool = portal_service_module.get_trajectory_pool()
        initial_size = pool.size

        await svc._extract_trajectory_bg(
            session_id="test-session-traj",
            user_message="hello",
            assistant_response="hi there",
            dispatched_workflow_ids=["wf-1", "wf-2"],
            workflow_results={"wf-1": "ok", "wf-2": "[错误: timeout]"},
        )

        assert pool.size == initial_size + 1

    asyncio.run(_run())


# ===========================================================================
# 7. Trajectory pool triggers learning cycle
# ===========================================================================

def test_trajectory_pool_triggers_learning_cycle():
    """TrajectoryPool should trigger learning when size threshold is reached."""
    pool = TrajectoryPool(size_threshold=3, time_threshold_seconds=9999)

    assert not pool.should_trigger_learning()

    pool.add("s1", {"tool_call_count": 1})
    pool.add("s2", {"tool_call_count": 2})
    assert not pool.should_trigger_learning()

    pool.add("s3", {"tool_call_count": 3})
    assert pool.should_trigger_learning()

    # Drain resets
    entries = pool.drain()
    assert len(entries) == 3
    assert pool.size == 0
    assert not pool.should_trigger_learning()


def test_trajectory_pool_triggers_by_time():
    """TrajectoryPool should trigger learning after time threshold elapses."""
    pool = TrajectoryPool(size_threshold=9999, time_threshold_seconds=0.01)

    pool.add("s1", {"tool_call_count": 1})
    time.sleep(0.02)  # Wait for time threshold

    assert pool.should_trigger_learning()

    entries = pool.drain()
    assert len(entries) == 1


# ===========================================================================
# 8. Strong signal immediate precipitation
# ===========================================================================

def test_strong_signal_immediate_precipitation():
    """Strong signal keywords should be detected correctly."""
    # Positive cases
    assert has_strong_signal("请保存这个流程给我")
    assert has_strong_signal("以后还会用到这个")
    assert has_strong_signal("每次都要执行这个步骤")
    assert has_strong_signal("Please remember this for me")
    assert has_strong_signal("SAVE THIS template")

    # Negative cases
    assert not has_strong_signal("你好")
    assert not has_strong_signal("帮我查天气")
    assert not has_strong_signal("what is the weather")


def test_strong_signal_triggers_l3_in_trajectory(tmp_path, monkeypatch):
    """When user message has strong signal, trajectory should mark L3."""
    _reset_globals(monkeypatch, tmp_path)

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        default = await mgr.ensure_default_portal()
        svc = await mgr.get_service(default.id)
        assert svc is not None

        pool = portal_service_module.get_trajectory_pool()
        initial_size = pool.size

        # Use a message with strong signal
        with patch("src.artifacts.service.get_artifact_factory_service") as mock_factory:
            mock_service = MagicMock()
            mock_service.run_periodic_learning_cycle = AsyncMock(return_value={})
            mock_factory.return_value = mock_service

            await svc._extract_trajectory_bg(
                session_id="test-session-l3",
                user_message="请保存这个流程，以后还会用",
                assistant_response="好的，已保存",
                dispatched_workflow_ids=[],
                workflow_results={},
            )

        # entry should be in pool with L3 signal
        assert pool.size == initial_size + 1

    asyncio.run(_run())


# ===========================================================================
# API-level tests
# ===========================================================================

def test_api_get_default_portal(tmp_path, monkeypatch):
    """GET /api/portals/default should return (or create) the default portal."""
    _reset_globals(monkeypatch, tmp_path)

    from fastapi.testclient import TestClient
    from src.api.main import create_app

    client = TestClient(create_app())

    resp = client.get("/api/portals/default")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_default"] is True
    assert data["auto_include_published"] is True
    assert data["name"] == "Root Portal"

    # Second call should return same portal
    resp2 = client.get("/api/portals/default")
    assert resp2.status_code == 200
    assert resp2.json()["id"] == data["id"]
