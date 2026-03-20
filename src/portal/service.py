"""
Portal Service — Core runtime for the Super Portal.

Lifecycle of a single user turn:
  1. Load/create session  (PortalSession)
  2. Retrieve relevant memories
  3. Call IntentUnderstandingService → WorkflowDispatchPlan list
  4. Execute workflows (parallel where priority matches, sequential otherwise)
  5. Synthesise results into a final answer via LLM
  6. Extract & store new memories from the conversation turn
  7. Persist session
  8. Stream PortalEvents back to caller
"""

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import uuid4

from ..core.models import (
    IntentUnderstandingResult,
    PortalConversationMessage,
    PortalEvent,
    PortalEventType,
    PortalMemoryEntry,
    PortalSession,
    SuperPortalConfig,
    WorkflowDispatchPlan,
)
from ..orchestration.workflow import WorkflowManager, get_workflow_manager
from ..storage.persistence import StorageManager, get_storage_manager
from .intent import IntentUnderstandingService
from .memory import PortalMemoryManager

logger = logging.getLogger(__name__)

PORTAL_COLLECTION = "portals"
SESSION_COLLECTION = "portal_sessions"

SYNTHESIS_SYSTEM_PROMPT = """You are a helpful assistant that synthesises results from multiple specialised workflows into a single, coherent, and well-formatted response.

Instructions:
- Integrate the workflow results naturally — do not just list them.
- Resolve any contradictions across workflows using good judgement.
- Be concise but complete.
- Use Markdown formatting where appropriate.
- Address the user directly.
"""


class PortalService:
    """
    Runtime service for a single Super Portal instance.

    One PortalService manages one SuperPortalConfig (identified by portal_id).
    Multiple portals can run in the same process via PortalManager.
    """

    def __init__(
        self,
        config: SuperPortalConfig,
        workflow_manager: WorkflowManager,
        storage: StorageManager,
    ):
        self.config = config
        self._wf_manager = workflow_manager
        self._storage = storage
        self._memory = PortalMemoryManager(storage)
        self._client = None   # lazy-initialised OpenAI client
        self._intent_svc: Optional[IntentUnderstandingService] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(
        self,
        user_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PortalSession:
        """Create a new conversation session."""
        session = PortalSession(
            session_id=str(uuid4()),
            portal_id=self.config.id,
            user_id=user_id,
            metadata=metadata or {},
        )
        await self._save_session(session)
        return session

    async def get_session(self, session_id: str) -> Optional[PortalSession]:
        """Load a session by ID."""
        return await self._load_session(session_id)

    async def chat(
        self,
        session_id: str,
        user_message: str,
        user_id: str = "default",
        stream: bool = True,
    ) -> AsyncIterator[PortalEvent]:
        """
        Process a user message and stream PortalEvents.

        Args:
            session_id: Existing or new session ID
            user_message: The user's raw message
            user_id: Caller user identifier
            stream: If False, buffer and yield events in batch (still async generator)

        Yields:
            PortalEvent objects
        """
        client = self._get_client()
        intent_svc = self._get_intent_service()

        # 1. Load or create session
        session = await self._load_session(session_id)
        if not session:
            session = PortalSession(
                session_id=session_id,
                portal_id=self.config.id,
                user_id=user_id,
            )

        # 2. Retrieve memories
        memories: List[PortalMemoryEntry] = []
        if self.config.memory_enabled:
            memories = await self._memory.retrieve(
                portal_id=self.config.id,
                user_id=user_id,
                query=user_message,
                top_k=10,
            )

        # 3. Build conversation history for context
        history = [
            {"role": m.role, "content": m.content}
            for m in session.messages[-(self.config.max_session_messages):]
        ]

        # 4. Get available workflows
        available_wfs = await self._get_available_workflows()
        if not available_wfs:
            session.messages.append(PortalConversationMessage(
                role="user", content=user_message,
            ))
            session.messages.append(PortalConversationMessage(
                role="assistant",
                content="⚠️ 当前超级入口还没有绑定任何工作流，请先在管理界面选择并绑定工作流。",
            ))
            await self._save_session(session)
            yield PortalEvent(
                type=PortalEventType.ERROR,
                session_id=session_id,
                portal_id=self.config.id,
                error="No workflows bound to this portal",
            )
            return

        # 5. Intent understanding (using the workflow-level alias)
        intent_result = await intent_svc.understand_workflows(
            user_query=user_message,
            available_workflows=available_wfs,
            conversation_history=history,
            memories=memories,
        )

        yield PortalEvent(
            type=PortalEventType.INTENT_UNDERSTOOD,
            session_id=session_id,
            portal_id=self.config.id,
            intent=intent_result,
        )

        # If clarification needed, just ask
        if intent_result.clarification_needed:
            q = intent_result.clarification_question or "能请您进一步说明需求吗？"
            session.messages.append(PortalConversationMessage(role="user", content=user_message))
            session.messages.append(PortalConversationMessage(role="assistant", content=q))
            await self._save_session(session)
            yield PortalEvent(
                type=PortalEventType.CONTENT,
                session_id=session_id,
                portal_id=self.config.id,
                delta=q,
            )
            yield PortalEvent(
                type=PortalEventType.COMPLETE,
                session_id=session_id,
                portal_id=self.config.id,
            )
            return

        # 6. Execute workflows (group by priority, execute same-priority in parallel)
        workflow_results: Dict[str, str] = {}  # workflow_id → result text

        plans_by_priority: Dict[int, List[WorkflowDispatchPlan]] = {}
        for plan in intent_result.dispatch_plans:
            plans_by_priority.setdefault(plan.priority, []).append(plan)

        dispatched_workflow_ids: List[str] = []

        for priority in sorted(plans_by_priority.keys()):
            group = plans_by_priority[priority]

            # Emit dispatch start events
            for plan in group:
                dispatched_workflow_ids.append(plan.workflow_id)
                yield PortalEvent(
                    type=PortalEventType.WORKFLOW_DISPATCH_START,
                    session_id=session_id,
                    portal_id=self.config.id,
                    workflow_id=plan.workflow_id,
                    workflow_name=plan.workflow_name,
                )

            # Execute in parallel within the same priority group
            tasks = [
                self._run_workflow(plan.workflow_id, plan.sub_query)
                for plan in group
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for plan, result in zip(group, results):
                if isinstance(result, Exception):
                    result_text = f"[工作流 {plan.workflow_name} 执行出错: {result}]"
                else:
                    result_text = result or "(无输出)"

                workflow_results[plan.workflow_id] = result_text

                yield PortalEvent(
                    type=PortalEventType.WORKFLOW_DISPATCH_RESULT,
                    session_id=session_id,
                    portal_id=self.config.id,
                    workflow_id=plan.workflow_id,
                    workflow_name=plan.workflow_name,
                    workflow_result=result_text[:500],  # preview in event
                )

        # 7. Synthesise final answer
        yield PortalEvent(
            type=PortalEventType.SYNTHESIS_START,
            session_id=session_id,
            portal_id=self.config.id,
        )

        final_answer = await self._synthesise(
            client=client,
            user_query=user_message,
            intent=intent_result.understood_intent,
            workflow_results=workflow_results,
            memories=memories,
            history=history,
            stream_callback=None,  # handled below
        )

        # Stream final answer character by character (chunked)
        if stream:
            async for chunk in self._stream_synthesis(
                client=client,
                user_query=user_message,
                intent=intent_result.understood_intent,
                workflow_results=workflow_results,
                memories=memories,
                history=history,
            ):
                yield PortalEvent(
                    type=PortalEventType.CONTENT,
                    session_id=session_id,
                    portal_id=self.config.id,
                    delta=chunk,
                )
            final_answer_for_session = await self._synthesise(
                client=client,
                user_query=user_message,
                intent=intent_result.understood_intent,
                workflow_results=workflow_results,
                memories=memories,
                history=history,
            )
        else:
            yield PortalEvent(
                type=PortalEventType.CONTENT,
                session_id=session_id,
                portal_id=self.config.id,
                delta=final_answer,
            )
            final_answer_for_session = final_answer

        # 8. Update session
        session.messages.append(PortalConversationMessage(
            role="user",
            content=user_message,
        ))
        session.messages.append(PortalConversationMessage(
            role="assistant",
            content=final_answer_for_session,
            dispatched_workflows=dispatched_workflow_ids,
        ))

        # Trim session history
        if len(session.messages) > self.config.max_session_messages * 2:
            session.messages = session.messages[-(self.config.max_session_messages * 2):]

        await self._save_session(session)

        # 9. Extract and store memories (non-blocking)
        if self.config.memory_enabled:
            asyncio.create_task(self._extract_memories(
                client=client,
                session=session,
                user_message=user_message,
                assistant_response=final_answer_for_session,
                user_id=user_id,
            ))

        yield PortalEvent(
            type=PortalEventType.COMPLETE,
            session_id=session_id,
            portal_id=self.config.id,
        )

    # ------------------------------------------------------------------
    # Memory public helpers
    # ------------------------------------------------------------------

    async def get_memories(
        self,
        user_id: str = "default",
        query: str = "",
        top_k: int = 20,
    ) -> List[PortalMemoryEntry]:
        """Retrieve memories for a user."""
        return await self._memory.retrieve(
            portal_id=self.config.id,
            user_id=user_id,
            query=query,
            top_k=top_k,
        )

    async def delete_memory(self, entry_id: str) -> bool:
        """Delete a specific memory entry."""
        return await self._memory.delete(entry_id)

    async def clear_memories(self, user_id: str = "default") -> int:
        """Clear all memories for a user."""
        return await self._memory.clear(self.config.id, user_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise RuntimeError("openai package is required. Install with: pip install openai")

            kwargs: Dict[str, Any] = {}
            api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY")
            if api_key:
                kwargs["api_key"] = api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            elif not api_key:
                kwargs["api_key"] = "placeholder"  # for Ollama-style providers

            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _get_intent_service(self) -> IntentUnderstandingService:
        if self._intent_svc is None:
            self._intent_svc = IntentUnderstandingService(
                llm_client=self._get_client(),
                model=self.config.model,
                temperature=self.config.temperature,
            )
        return self._intent_svc

    async def _get_available_workflows(self) -> List[Dict[str, Any]]:
        """Return metadata for all workflows bound to this portal."""
        result = []
        for wf_id in self.config.workflow_ids:
            wf = await self._wf_manager.get_workflow(wf_id)
            if wf:
                result.append({
                    "id": wf.id,
                    "name": wf.name,
                    "description": wf.description,
                })
        return result

    async def _run_workflow(self, workflow_id: str, sub_query: str) -> str:
        """Execute a single workflow and return its text output."""
        try:
            result = await self._wf_manager.run_workflow(workflow_id, sub_query)
            if result.error:
                return f"[错误: {result.error}]"
            if result.response and result.response.messages:
                return "\n".join(m.content for m in result.response.messages if m.content)
            return "(工作流未返回内容)"
        except Exception as e:
            logger.error(f"[Portal] Workflow {workflow_id} execution error: {e}")
            return f"[执行异常: {e}]"

    async def _synthesise(
        self,
        client,
        user_query: str,
        intent: str,
        workflow_results: Dict[str, str],
        memories: List[PortalMemoryEntry],
        history: List[Dict[str, str]],
        stream_callback=None,
    ) -> str:
        """Call LLM to synthesise a final answer (non-streaming)."""
        user_content = self._build_synthesis_prompt(
            user_query, intent, workflow_results, memories
        )
        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            *history[-6:],
            {"role": "user", "content": user_content},
        ]
        try:
            resp = await client.chat.completions.create(
                model=self.config.model,
                temperature=0.5,
                messages=messages,
                max_tokens=2048,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"[Portal] Synthesis failed: {e}")
            # Fallback: concatenate workflow results
            parts = [f"**{wf_id}**: {text}" for wf_id, text in workflow_results.items()]
            return "\n\n".join(parts)

    async def _stream_synthesis(
        self,
        client,
        user_query: str,
        intent: str,
        workflow_results: Dict[str, str],
        memories: List[PortalMemoryEntry],
        history: List[Dict[str, str]],
    ) -> AsyncIterator[str]:
        """Stream synthesis chunks."""
        user_content = self._build_synthesis_prompt(
            user_query, intent, workflow_results, memories
        )
        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            *history[-6:],
            {"role": "user", "content": user_content},
        ]
        try:
            stream = await client.chat.completions.create(
                model=self.config.model,
                temperature=0.5,
                messages=messages,
                max_tokens=2048,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"[Portal] Stream synthesis failed: {e}")
            for wf_id, text in workflow_results.items():
                yield f"\n**{wf_id}**: {text}"

    @staticmethod
    def _build_synthesis_prompt(
        user_query: str,
        intent: str,
        workflow_results: Dict[str, str],
        memories: List[PortalMemoryEntry],
    ) -> str:
        memory_block = "\n".join(f"- {m.content}" for m in memories) or "(none)"
        results_block = "\n\n".join(
            f"### Workflow `{wf_id}` result:\n{text}"
            for wf_id, text in workflow_results.items()
        ) or "(no results)"

        return f"""User intent: {intent}

User memory context:
{memory_block}

Workflow results:
{results_block}

Original user message:
{user_query}

Please synthesise the above into a helpful, integrated response."""

    async def _extract_memories(
        self,
        client,
        session: PortalSession,
        user_message: str,
        assistant_response: str,
        user_id: str,
    ) -> None:
        """Background task: extract memories from the latest conversation turn."""
        try:
            turn_text = f"User: {user_message}\nAssistant: {assistant_response}"
            new_memories = await self._memory.extract_and_store(
                portal_id=self.config.id,
                user_id=user_id,
                conversation_turn=turn_text,
                session_id=session.session_id,
                llm_client=client,
                model=self.config.model,
            )

            # Prune if over limit
            if self.config.memory_enabled:
                await self._memory.prune(
                    portal_id=self.config.id,
                    user_id=user_id,
                    max_entries=self.config.max_memory_entries,
                    importance_threshold=self.config.memory_importance_threshold,
                )

            logger.debug(f"[Portal] Extracted {len(new_memories)} memories")
        except Exception as e:
            logger.error(f"[Portal] Memory extraction error: {e}")

    async def _save_session(self, session: PortalSession) -> None:
        session.updated_at = datetime.now()
        data = session.model_dump()
        for field in ("created_at", "updated_at"):
            if isinstance(data.get(field), datetime):
                data[field] = data[field].isoformat()
        # Serialise nested datetimes in messages
        for msg in data.get("messages", []):
            if isinstance(msg.get("timestamp"), datetime):
                msg["timestamp"] = msg["timestamp"].isoformat()
        await self._storage.backend.save(SESSION_COLLECTION, session.session_id, data)

    async def _load_session(self, session_id: str) -> Optional[PortalSession]:
        try:
            data = await self._storage.backend.load(SESSION_COLLECTION, session_id)
            if data:
                return PortalSession(**data)
        except Exception as e:
            logger.warning(f"[Portal] Session load failed: {e}")
        return None


# ============================================================
# Portal Manager — manages multiple portal instances
# ============================================================

class PortalManager:
    """
    Manages the lifecycle of all Super Portal instances.

    - CRUD for SuperPortalConfig (persisted in storage)
    - Creates PortalService instances on demand
    """

    def __init__(self):
        self._portals: Dict[str, SuperPortalConfig] = {}
        self._services: Dict[str, PortalService] = {}
        self._storage: Optional[StorageManager] = None
        self._wf_manager: Optional[WorkflowManager] = None
        self._loaded = False

    async def _ensure_ready(self):
        if self._storage is None:
            from ..storage import initialize_storage
            self._storage = await initialize_storage()
        if self._wf_manager is None:
            self._wf_manager = get_workflow_manager()
            await self._wf_manager._ensure_storage()
        if not self._loaded:
            await self._load_all()
            self._loaded = True

    async def _load_all(self):
        try:
            items = await self._storage.backend.list_all(PORTAL_COLLECTION)
            for item in items:
                try:
                    cfg = SuperPortalConfig(**item)
                    self._portals[cfg.id] = cfg
                except Exception as e:
                    logger.warning(f"[PortalManager] Skipping malformed portal: {e}")
            logger.info(f"[PortalManager] Loaded {len(self._portals)} portals")
        except Exception as e:
            logger.error(f"[PortalManager] Load failed: {e}")

    async def _save_config(self, config: SuperPortalConfig):
        data = config.model_dump()
        for field in ("created_at", "updated_at"):
            if isinstance(data.get(field), datetime):
                data[field] = data[field].isoformat()
        await self._storage.backend.save(PORTAL_COLLECTION, config.id, data)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_portal(
        self,
        name: str,
        description: str = "",
        workflow_ids: Optional[List[str]] = None,
        provider: str = "openai",
        model: str = "gpt-4",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> SuperPortalConfig:
        """Create and persist a new Super Portal configuration."""
        await self._ensure_ready()

        config = SuperPortalConfig(
            id=str(uuid4()),
            name=name,
            description=description,
            workflow_ids=workflow_ids or [],
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            api_key_access=f"portal_{secrets.token_urlsafe(24)}",
        )

        self._portals[config.id] = config
        await self._save_config(config)
        logger.info(f"[PortalManager] Created portal {config.id}: {name}")
        return config

    async def get_portal(self, portal_id: str) -> Optional[SuperPortalConfig]:
        await self._ensure_ready()
        return self._portals.get(portal_id)

    async def list_portals(self) -> List[SuperPortalConfig]:
        await self._ensure_ready()
        return list(self._portals.values())

    async def update_portal(
        self,
        portal_id: str,
        updates: Dict[str, Any],
    ) -> Optional[SuperPortalConfig]:
        await self._ensure_ready()
        config = self._portals.get(portal_id)
        if not config:
            return None

        allowed = {
            "name", "description", "workflow_ids",
            "provider", "model", "api_key", "base_url",
            "memory_enabled", "max_memory_entries", "public",
        }
        for k, v in updates.items():
            if k in allowed:
                setattr(config, k, v)

        config.updated_at = datetime.now()
        # Invalidate cached service so it picks up new config
        self._services.pop(portal_id, None)

        await self._save_config(config)
        return config

    async def delete_portal(self, portal_id: str) -> bool:
        await self._ensure_ready()
        if portal_id in self._portals:
            del self._portals[portal_id]
            self._services.pop(portal_id, None)
            await self._storage.backend.delete(PORTAL_COLLECTION, portal_id)
            logger.info(f"[PortalManager] Deleted portal {portal_id}")
            return True
        return False

    # ------------------------------------------------------------------
    # Service access
    # ------------------------------------------------------------------

    async def get_service(self, portal_id: str) -> Optional[PortalService]:
        """Get (or lazily create) the PortalService for a portal."""
        await self._ensure_ready()
        config = self._portals.get(portal_id)
        if not config:
            return None

        if portal_id not in self._services:
            self._services[portal_id] = PortalService(
                config=config,
                workflow_manager=self._wf_manager,
                storage=self._storage,
            )

        return self._services[portal_id]

    async def get_by_access_key(self, access_key: str) -> Optional[SuperPortalConfig]:
        """Lookup portal by its API access key."""
        await self._ensure_ready()
        for cfg in self._portals.values():
            if cfg.api_key_access == access_key:
                return cfg
        return None


# ------------------------------------------------------------------
# Global singleton
# ------------------------------------------------------------------

_global_portal_manager: Optional[PortalManager] = None


def get_portal_manager() -> PortalManager:
    global _global_portal_manager
    if _global_portal_manager is None:
        _global_portal_manager = PortalManager()
    return _global_portal_manager
