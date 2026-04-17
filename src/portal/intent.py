"""
Intent Understanding Service — Platform-level capability.

This service can be used at any level of the tree:
- By SuperPortal to pick which published workflows to call
- By any AgentNode whose routing_strategy == INTENT to pick which child agents to call

The service is stateless: it receives everything it needs per call.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from ..core.models import (
    IntentUnderstandingResult,
    PortalMemoryEntry,
    WorkflowDispatchPlan,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Shared system prompt — works for both workflows and child agents   #
# ------------------------------------------------------------------ #
INTENT_SYSTEM_PROMPT = """You are an intelligent request router embedded inside an AI agent tree.

Your job is to:
1. Understand the user's true intent given their message, conversation history, and any remembered context.
2. Decide which of the available children (agents or workflows) can best fulfil the request.
3. For each selected child, craft a precise sub-query tailored to that child's specialty.
4. If the request is ambiguous AND cannot be reasonably resolved from context, set clarification_needed=true.

RULES:
- Select children ONLY when semantically relevant to the user request.
- If none are relevant, return an empty dispatch_plans array.
- Children with the same priority value will be executed in PARALLEL; use different priorities to force sequential execution.
- Keep sub-queries focused — do NOT pad them with unrelated information.
- You may select multiple children if the request spans multiple domains.
- Output ONLY valid JSON matching the schema below. No markdown fences, no explanation.

Output JSON schema:
{
  "understood_intent": "<one-sentence summary of what the user wants>",
  "dispatch_plans": [
    {
      "workflow_id": "<child_id>",
      "workflow_name": "<child_name>",
      "sub_query": "<refined query for this child>",
      "reason": "<why this child was chosen>",
      "priority": <int — 0 = highest; same value = parallel>,
      "relevance_score": <float 0..1>
    }
  ],
  "clarification_needed": <bool>,
  "clarification_question": "<question to ask user, or null>"
}
"""


class IntentUnderstandingService:
    """
    Platform-level intent understanding & child routing capability.

    Stateless — receives all required data per call.
    Can route to workflow IDs (used by Portal) or agent IDs (used by tree nodes
    with routing_strategy=INTENT).
    """

    def __init__(self, llm_client, model: str = "gpt-4", temperature: float = 0.2):
        self._client = llm_client
        self._model = model
        self._temperature = temperature

    async def understand(
        self,
        user_query: str,
        available_children: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        memories: Optional[List[PortalMemoryEntry]] = None,
        memory_snapshot: str = "",
        session_retrievals: Optional[List[Dict[str, Any]]] = None,
        max_selected: int = 0,
        min_relevance_score: float = 0.0,
    ) -> IntentUnderstandingResult:
        """
        Analyse a user query and produce a dispatch plan.

        Args:
            user_query:
                The raw message from the user (or the last message in context).
            available_children:
                List of dicts describing the available routing targets.
                Each dict must have: id, name, description.
                (Previously called available_workflows — renamed for generality.)
            conversation_history:
                Recent messages [{"role": ..., "content": ...}].
            memories:
                Relevant PortalMemoryEntry objects for this user (optional).
            max_selected:
                If > 0, instruct the LLM to select at most this many children.
                0 means no limit.

        Returns:
            IntentUnderstandingResult with dispatch plans.
        """
        children_block = self._format_children(available_children)
        memory_block = self._format_memories(memories or [])
        bounded_memory_block = memory_snapshot.strip() or "(no bounded snapshot)"
        history_block = self._format_history(conversation_history or [])
        session_retrieval_block = self._format_session_retrievals(session_retrievals or [])
        limit_note = (
            f"\nNOTE: Select at most {max_selected} children.\n"
            if max_selected > 0 else ""
        )

        user_content = (
            f"## Available Children (agents / workflows)\n{children_block}\n\n"
            f"## Bounded Memory Snapshot\n{bounded_memory_block}\n\n"
            f"## User Long-term Memory\n{memory_block}\n\n"
            f"## Session Retrieval Snippets\n{session_retrieval_block}\n\n"
            f"## Recent Conversation History\n{history_block}\n"
            f"{limit_note}"
            f"\n## Current User Message\n{user_query}\n\n"
            "Analyse the above and produce the JSON routing plan."
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=1024,
            )

            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            data = json.loads(raw)

            raw_dispatch_plans = [
                WorkflowDispatchPlan(**p) for p in data.get("dispatch_plans", [])
            ]
            dispatch_plans = list(raw_dispatch_plans)
            filtered_by_relevance = 0

            # Filter low-relevance selections (if threshold enabled).
            if min_relevance_score > 0:
                before = len(dispatch_plans)
                dispatch_plans = [
                    p for p in dispatch_plans
                    if (p.relevance_score is not None and p.relevance_score >= min_relevance_score)
                ]
                filtered_by_relevance = max(0, before - len(dispatch_plans))

            # Enforce max_selected limit if LLM ignored it
            if max_selected > 0 and len(dispatch_plans) > max_selected:
                dispatch_plans = dispatch_plans[:max_selected]

            memory_ids = [m.id for m in (memories or [])]
            if dispatch_plans:
                routing_status = "matched"
                routing_note = None
            elif filtered_by_relevance > 0:
                routing_status = "filtered_by_relevance"
                routing_note = (
                    f"all {filtered_by_relevance} candidate(s) below relevance threshold {min_relevance_score}"
                )
            elif data.get("clarification_needed", False):
                routing_status = "no_match"
                routing_note = "clarification required before dispatch"
            else:
                routing_status = "no_match"
                routing_note = "no semantically relevant child"

            result = IntentUnderstandingResult(
                original_query=user_query,
                understood_intent=data.get("understood_intent", user_query),
                dispatch_plans=dispatch_plans,
                clarification_needed=data.get("clarification_needed", False),
                clarification_question=data.get("clarification_question"),
                memories_used=memory_ids,
                routing_status=routing_status,
                routing_note=routing_note,
            )

            logger.info(
                f"[Intent] Understood: '{result.understood_intent}' → "
                f"{len(dispatch_plans)} child(ren) selected | "
                f"status={routing_status} | note={routing_note}"
            )
            return result

        except Exception as e:
            logger.error(f"[Intent] Understanding failed: {e}")
            # Safe fallback: do not force dispatch when intent parsing fails.
            return IntentUnderstandingResult(
                original_query=user_query,
                understood_intent=user_query,
                dispatch_plans=[],
                routing_status="intent_error",
                routing_note=f"intent parsing failed: {str(e)[:200]}",
            )

    # ------------------------------------------------------------------ #
    #  Convenience alias — keeps Portal code readable                     #
    # ------------------------------------------------------------------ #
    async def understand_workflows(
        self,
        user_query: str,
        available_workflows: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        memories: Optional[List[PortalMemoryEntry]] = None,
        memory_snapshot: str = "",
        session_retrievals: Optional[List[Dict[str, Any]]] = None,
        max_selected: int = 0,
        min_relevance_score: float = 0.0,
    ) -> IntentUnderstandingResult:
        """Alias for understand() — used by Portal for workflow-level routing."""
        return await self.understand(
            user_query=user_query,
            available_children=available_workflows,
            conversation_history=conversation_history,
            memories=memories,
            memory_snapshot=memory_snapshot,
            session_retrievals=session_retrievals,
            max_selected=max_selected,
            min_relevance_score=min_relevance_score,
        )

    # ------------------------------------------------------------------ #
    #  Formatting helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_children(children: List[Dict[str, Any]]) -> str:
        if not children:
            return "(none)"
        lines = []
        for c in children:
            lines.append(
                f"- id={c['id']!r}  name={c.get('name', '')!r}\n"
                f"  description: {c.get('description', 'No description')}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_memories(memories: List[PortalMemoryEntry]) -> str:
        if not memories:
            return "(no memories yet)"
        return "\n".join(f"- [{m.memory_type}] {m.content}" for m in memories)

    @staticmethod
    def _format_history(history: List[Dict[str, str]]) -> str:
        if not history:
            return "(no history)"
        lines = []
        for msg in history[-10:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            lines.append(f"{role.upper()}: {content[:300]}")
        return "\n".join(lines)

    @staticmethod
    def _format_session_retrievals(results: List[Dict[str, Any]]) -> str:
        if not results:
            return "(no related snippets)"
        lines = []
        for item in results[:8]:
            sid = item.get("session_id", "?")
            role = item.get("role", "?")
            snippet = item.get("snippet", "")
            lines.append(f"- session={sid} role={role}: {snippet}")
        return "\n".join(lines)
