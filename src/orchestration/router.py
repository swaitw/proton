"""
Router for directing messages to appropriate agents.

The router handles:
- Conditional routing based on message content
- Intent classification
- Load balancing
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

from ..core.models import ChatMessage, AgentResponse, RoutingStrategy
from ..core.agent_node import AgentNode
from ..core.context import ExecutionContext

logger = logging.getLogger(__name__)


class ConditionType(str, Enum):
    """Types of routing conditions."""
    KEYWORD = "keyword"          # Simple keyword matching
    REGEX = "regex"              # Regular expression
    INTENT = "intent"            # Intent classification
    CUSTOM = "custom"            # Custom function


@dataclass
class RoutingCondition:
    """A condition for routing to a specific agent."""
    type: ConditionType
    pattern: str                  # Keyword, regex, or intent name
    target_id: str               # Target agent ID
    priority: int = 0            # Higher priority matched first
    custom_func: Optional[Callable[[ChatMessage], bool]] = None


@dataclass
class RouterConfig:
    """Configuration for the router."""
    default_target: Optional[str] = None
    conditions: List[RoutingCondition] = field(default_factory=list)
    fallback_strategy: str = "first"  # first, random, round_robin
    use_llm_classifier: bool = False
    classifier_model: str = "gpt-4"


class Router:
    """
    Routes messages to appropriate agents based on conditions.

    Supports:
    - Keyword matching
    - Regex patterns
    - Intent classification (via LLM)
    - Custom routing functions
    """

    def __init__(self, config: RouterConfig):
        self.config = config
        self._conditions = sorted(
            config.conditions,
            key=lambda c: c.priority,
            reverse=True
        )
        self._classifier = None
        self._round_robin_index = 0

    async def route(
        self,
        message: ChatMessage,
        available_agents: List[AgentNode],
        context: Optional[ExecutionContext] = None,
    ) -> Optional[str]:
        """
        Determine which agent should handle the message.

        Args:
            message: The incoming message
            available_agents: List of available agent nodes
            context: Optional execution context

        Returns:
            Agent ID to route to, or None if no match
        """
        available_ids = {a.id for a in available_agents}
        content = message.content.lower()

        # Check each condition in priority order
        for condition in self._conditions:
            if condition.target_id not in available_ids:
                continue

            if self._matches_condition(message, condition):
                logger.debug(f"Routed to {condition.target_id} via {condition.type}")
                return condition.target_id

        # Use LLM classifier if enabled
        if self.config.use_llm_classifier:
            target = await self._classify_with_llm(message, available_agents)
            if target:
                return target

        # Fallback
        return self._get_fallback_target(available_agents)

    def _matches_condition(
        self,
        message: ChatMessage,
        condition: RoutingCondition,
    ) -> bool:
        """Check if a message matches a routing condition."""
        content = message.content

        if condition.type == ConditionType.KEYWORD:
            return condition.pattern.lower() in content.lower()

        elif condition.type == ConditionType.REGEX:
            try:
                return bool(re.search(condition.pattern, content, re.IGNORECASE))
            except re.error:
                logger.warning(f"Invalid regex pattern: {condition.pattern}")
                return False

        elif condition.type == ConditionType.INTENT:
            # Intent matching requires the message to have intent metadata
            intent = message.metadata.get("intent")
            return intent == condition.pattern if intent else False

        elif condition.type == ConditionType.CUSTOM:
            if condition.custom_func:
                try:
                    return condition.custom_func(message)
                except Exception as e:
                    logger.error(f"Custom condition error: {e}")
                    return False

        return False

    async def _classify_with_llm(
        self,
        message: ChatMessage,
        available_agents: List[AgentNode],
    ) -> Optional[str]:
        """Use an LLM to classify which agent should handle the message."""
        try:
            # Build classification prompt
            agent_descriptions = "\n".join([
                f"- {a.name} ({a.id}): {a.description}"
                for a in available_agents
            ])

            prompt = f"""Given the following user message and available agents,
determine which agent is best suited to handle the request.

User message: {message.content}

Available agents:
{agent_descriptions}

Respond with only the agent ID that should handle this message."""

            # This would use the native adapter or direct API call
            # For now, return None to fall back
            logger.debug("LLM classification not implemented, using fallback")
            return None

        except Exception as e:
            logger.error(f"LLM classification error: {e}")
            return None

    def _get_fallback_target(
        self,
        available_agents: List[AgentNode],
    ) -> Optional[str]:
        """Get fallback target when no conditions match."""
        if not available_agents:
            return None

        # Check configured default
        if self.config.default_target:
            for agent in available_agents:
                if agent.id == self.config.default_target:
                    return agent.id

        # Apply fallback strategy
        if self.config.fallback_strategy == "first":
            return available_agents[0].id

        elif self.config.fallback_strategy == "random":
            import random
            return random.choice(available_agents).id

        elif self.config.fallback_strategy == "round_robin":
            target = available_agents[self._round_robin_index % len(available_agents)]
            self._round_robin_index += 1
            return target.id

        return available_agents[0].id if available_agents else None

    def add_condition(self, condition: RoutingCondition) -> None:
        """Add a routing condition."""
        self._conditions.append(condition)
        self._conditions.sort(key=lambda c: c.priority, reverse=True)

    def remove_condition(self, target_id: str) -> None:
        """Remove all conditions for a target."""
        self._conditions = [c for c in self._conditions if c.target_id != target_id]

    def add_keyword_route(
        self,
        keyword: str,
        target_id: str,
        priority: int = 0,
    ) -> None:
        """Convenience method to add a keyword-based route."""
        self.add_condition(RoutingCondition(
            type=ConditionType.KEYWORD,
            pattern=keyword,
            target_id=target_id,
            priority=priority,
        ))

    def add_regex_route(
        self,
        pattern: str,
        target_id: str,
        priority: int = 0,
    ) -> None:
        """Convenience method to add a regex-based route."""
        self.add_condition(RoutingCondition(
            type=ConditionType.REGEX,
            pattern=pattern,
            target_id=target_id,
            priority=priority,
        ))

    def add_intent_route(
        self,
        intent: str,
        target_id: str,
        priority: int = 0,
    ) -> None:
        """Convenience method to add an intent-based route."""
        self.add_condition(RoutingCondition(
            type=ConditionType.INTENT,
            pattern=intent,
            target_id=target_id,
            priority=priority,
        ))


class IntentClassifier:
    """
    Classifies user intents for routing.

    Can use:
    - Keyword matching
    - ML models
    - LLM-based classification
    """

    def __init__(self, intents: Dict[str, List[str]]):
        """
        Initialize with intent definitions.

        Args:
            intents: Dict mapping intent names to example phrases
        """
        self.intents = intents
        self._keyword_map: Dict[str, str] = {}

        # Build keyword map from examples
        for intent, examples in intents.items():
            for example in examples:
                for word in example.lower().split():
                    if len(word) > 3:  # Skip short words
                        self._keyword_map[word] = intent

    def classify(self, text: str) -> Tuple[Optional[str], float]:
        """
        Classify text into an intent.

        Args:
            text: Text to classify

        Returns:
            Tuple of (intent_name, confidence)
        """
        text_lower = text.lower()
        intent_scores: Dict[str, int] = {}

        # Simple keyword matching
        for word, intent in self._keyword_map.items():
            if word in text_lower:
                intent_scores[intent] = intent_scores.get(intent, 0) + 1

        if not intent_scores:
            return None, 0.0

        # Return highest scoring intent
        best_intent = max(intent_scores, key=intent_scores.get)
        confidence = min(intent_scores[best_intent] / 5.0, 1.0)

        return best_intent, confidence
