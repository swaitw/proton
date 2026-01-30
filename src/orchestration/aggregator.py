"""
Aggregator for combining results from multiple agents.

Handles:
- Merging responses from parallel execution
- Summarizing multiple outputs
- Conflict resolution
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ..core.models import AgentResponse, ChatMessage, MessageRole

logger = logging.getLogger(__name__)


class AggregationStrategy(str, Enum):
    """Strategies for aggregating multiple agent responses."""
    CONCAT = "concat"              # Concatenate all responses
    MERGE = "merge"                # Merge into single response
    VOTE = "vote"                  # Majority voting
    BEST = "best"                  # Select best based on criteria
    SUMMARIZE = "summarize"        # LLM summarization
    CUSTOM = "custom"              # Custom aggregation function


@dataclass
class AggregatorConfig:
    """Configuration for the aggregator."""
    strategy: AggregationStrategy = AggregationStrategy.CONCAT
    separator: str = "\n\n---\n\n"
    include_source: bool = True
    summarize_model: str = "gpt-4"
    max_output_length: int = 4096
    custom_func: Optional[Callable[[List[AgentResponse]], AgentResponse]] = None


class Aggregator:
    """
    Aggregates responses from multiple agents.

    Used when:
    - Parallel execution produces multiple results
    - Hierarchical decomposition needs to combine sub-results
    - Multiple specialists provide different perspectives
    """

    def __init__(self, config: Optional[AggregatorConfig] = None):
        self.config = config or AggregatorConfig()

    def aggregate(
        self,
        responses: List[AgentResponse],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        """
        Aggregate multiple agent responses into one.

        Args:
            responses: List of responses to aggregate
            metadata: Optional metadata about the aggregation

        Returns:
            Single aggregated AgentResponse
        """
        if not responses:
            return AgentResponse(
                messages=[],
                response_id="empty_aggregation",
            )

        if len(responses) == 1:
            return responses[0]

        strategy = self.config.strategy

        if strategy == AggregationStrategy.CONCAT:
            return self._concat(responses)
        elif strategy == AggregationStrategy.MERGE:
            return self._merge(responses)
        elif strategy == AggregationStrategy.VOTE:
            return self._vote(responses)
        elif strategy == AggregationStrategy.BEST:
            return self._select_best(responses)
        elif strategy == AggregationStrategy.SUMMARIZE:
            return self._summarize(responses)
        elif strategy == AggregationStrategy.CUSTOM:
            if self.config.custom_func:
                return self.config.custom_func(responses)
            return self._concat(responses)
        else:
            return self._concat(responses)

    def _concat(self, responses: List[AgentResponse]) -> AgentResponse:
        """Concatenate all responses."""
        all_messages: List[ChatMessage] = []
        all_tool_calls = []
        all_tool_results = []

        for resp in responses:
            if self.config.include_source:
                # Add source header
                for msg in resp.messages:
                    source_msg = ChatMessage(
                        role=msg.role,
                        content=msg.content,
                        name=msg.name,
                        metadata={**msg.metadata, "source_response_id": resp.response_id},
                    )
                    all_messages.append(source_msg)
            else:
                all_messages.extend(resp.messages)

            all_tool_calls.extend(resp.tool_calls)
            all_tool_results.extend(resp.tool_results)

        return AgentResponse(
            messages=all_messages,
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            response_id="aggregated",
            metadata={
                "aggregation_strategy": "concat",
                "source_count": len(responses),
            },
        )

    def _merge(self, responses: List[AgentResponse]) -> AgentResponse:
        """Merge responses into a single coherent response."""
        # Collect all assistant messages
        contents = []
        for resp in responses:
            for msg in resp.messages:
                if msg.role == MessageRole.ASSISTANT:
                    source = f"[{msg.name}]" if msg.name and self.config.include_source else ""
                    contents.append(f"{source} {msg.content}".strip())

        merged_content = self.config.separator.join(contents)

        # Truncate if needed
        if len(merged_content) > self.config.max_output_length:
            merged_content = merged_content[:self.config.max_output_length] + "..."

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=merged_content,
                name="aggregated",
            )],
            response_id="merged",
            metadata={
                "aggregation_strategy": "merge",
                "source_count": len(responses),
            },
        )

    def _vote(self, responses: List[AgentResponse]) -> AgentResponse:
        """
        Select response based on voting (for classification tasks).

        This works best when agents are producing categorical outputs.
        """
        # Extract main content from each response
        votes: Dict[str, int] = {}
        response_map: Dict[str, AgentResponse] = {}

        for resp in responses:
            if resp.messages:
                content = resp.messages[-1].content.strip().lower()
                votes[content] = votes.get(content, 0) + 1
                response_map[content] = resp

        if not votes:
            return self._concat(responses)

        # Find majority
        winner = max(votes, key=votes.get)
        winning_response = response_map[winner]

        return AgentResponse(
            messages=winning_response.messages,
            tool_calls=winning_response.tool_calls,
            tool_results=winning_response.tool_results,
            response_id="voted",
            metadata={
                "aggregation_strategy": "vote",
                "vote_count": votes[winner],
                "total_votes": len(responses),
                "vote_distribution": votes,
            },
        )

    def _select_best(self, responses: List[AgentResponse]) -> AgentResponse:
        """
        Select the best response based on criteria.

        Default: longest non-error response
        """
        best_response = None
        best_score = -1

        for resp in responses:
            # Skip error responses
            if resp.metadata.get("error"):
                continue

            # Score by content length (simple heuristic)
            score = sum(len(msg.content) for msg in resp.messages)

            if score > best_score:
                best_score = score
                best_response = resp

        if best_response is None:
            best_response = responses[0]

        return AgentResponse(
            messages=best_response.messages,
            tool_calls=best_response.tool_calls,
            tool_results=best_response.tool_results,
            response_id="best",
            metadata={
                **best_response.metadata,
                "aggregation_strategy": "best",
                "selected_score": best_score,
            },
        )

    def _summarize(self, responses: List[AgentResponse]) -> AgentResponse:
        """
        Use an LLM to summarize multiple responses.

        Note: This is a placeholder. In production, you would
        call an actual LLM to produce a summary.
        """
        # Collect all content
        contents = []
        for resp in responses:
            for msg in resp.messages:
                if msg.role == MessageRole.ASSISTANT:
                    source = f"({msg.name})" if msg.name else ""
                    contents.append(f"{source}: {msg.content}")

        # For now, just concatenate with a note
        combined = "\n\n".join(contents)

        return AgentResponse(
            messages=[ChatMessage(
                role=MessageRole.ASSISTANT,
                content=f"[Summary of {len(responses)} responses]\n\n{combined}",
                name="summarizer",
            )],
            response_id="summarized",
            metadata={
                "aggregation_strategy": "summarize",
                "source_count": len(responses),
                "note": "LLM summarization not implemented, using concat",
            },
        )


class ResponseEvaluator:
    """
    Evaluates and scores agent responses for selection.

    Criteria can include:
    - Relevance to query
    - Response length
    - Sentiment/tone
    - Factual accuracy (with verification)
    """

    def __init__(self, criteria: Optional[Dict[str, float]] = None):
        """
        Initialize with scoring criteria weights.

        Args:
            criteria: Dict of criterion_name -> weight
        """
        self.criteria = criteria or {
            "length": 0.3,
            "completeness": 0.4,
            "no_error": 0.3,
        }

    def score(self, response: AgentResponse, query: Optional[str] = None) -> float:
        """
        Score a response based on configured criteria.

        Args:
            response: The response to score
            query: Optional original query for relevance scoring

        Returns:
            Score between 0 and 1
        """
        total_score = 0.0
        total_weight = 0.0

        for criterion, weight in self.criteria.items():
            score = self._score_criterion(response, criterion, query)
            total_score += score * weight
            total_weight += weight

        return total_score / total_weight if total_weight > 0 else 0.0

    def _score_criterion(
        self,
        response: AgentResponse,
        criterion: str,
        query: Optional[str],
    ) -> float:
        """Score a single criterion."""
        if criterion == "length":
            # Score based on reasonable length (100-1000 chars ideal)
            total_len = sum(len(m.content) for m in response.messages)
            if total_len < 50:
                return 0.3
            elif total_len < 100:
                return 0.6
            elif total_len < 1000:
                return 1.0
            elif total_len < 2000:
                return 0.8
            else:
                return 0.5

        elif criterion == "completeness":
            # Check if response has content
            has_content = any(m.content.strip() for m in response.messages)
            return 1.0 if has_content else 0.0

        elif criterion == "no_error":
            # Check for error indicators
            has_error = response.metadata.get("error") or \
                        any("error" in m.content.lower() for m in response.messages)
            return 0.0 if has_error else 1.0

        return 0.5  # Default neutral score
