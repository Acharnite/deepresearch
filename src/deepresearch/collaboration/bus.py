"""CollaborationBus — Thread-safe in-memory shared knowledge repository.

Shared-nothing-writes, shared-all-reads: agents cannot modify each other's
data. All mutations are guarded by ``asyncio.Lock`` so the bus is safe to
use from concurrent agent coroutines.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any

from deepresearch.models import (
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    IndividualReport,
    ResearchTopic,
    SharedKnowledge,
)

logger = logging.getLogger(__name__)


class CollaborationBus:
    """In-memory shared knowledge repository for multi-agent collaboration.

    Lifecycle (aligned with ``Orchestrator`` states):
        1. ``topic`` is set after configuration.
        2. After Round 1 each agent calls ``publish_round_1``.
        3. The orchestrator calls ``compute_shared_knowledge`` to aggregate.
        4. After review each agent calls ``publish_followup``.
        5. After Round 2 each agent calls ``publish_round_2``.
        6. After report writing each agent calls ``publish_report``.
        7. (Optional) Clarification pairs can be added at any time.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.topic: ResearchTopic | None = None
        self.round_1_findings: dict[str, Findings] = {}
        self.shared_knowledge: SharedKnowledge | None = None
        self.followup_questions: dict[str, list[str]] = {}
        self.round_2_findings: dict[str, Findings] = {}
        self.other_rounds_findings: dict[tuple[str, int], Findings] = {}
        self.individual_reports: dict[str, IndividualReport] = {}
        self.clarifications: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Round 1 — publish / read
    # ------------------------------------------------------------------

    async def publish_round_1(self, agent_id: str, findings: Findings) -> None:
        """Publish an agent's Round 1 findings.

        Thread-safe: uses ``asyncio.Lock``.
        Echo-prevention: agents can only write their own data — the
        ``agent_id`` must match ``findings.agent_id``.
        """
        async with self._lock:
            if findings.agent_id != agent_id:
                logger.warning(
                    "Agent '%s' tried to publish findings for '%s' — ignored",
                    agent_id,
                    findings.agent_id,
                )
                return
            self.round_1_findings[agent_id] = findings
            logger.debug("Round 1 findings published for agent '%s'", agent_id)

    async def get_round_1_findings(self) -> dict[str, Findings]:
        """Return all Round 1 findings (shared-all-reads)."""
        async with self._lock:
            return dict(self.round_1_findings)

    # ------------------------------------------------------------------
    # Shared Knowledge — aggregation
    # ------------------------------------------------------------------

    async def compute_shared_knowledge(self) -> SharedKnowledge:
        """Aggregate all Round 1 findings into a ``SharedKnowledge`` object.

        The aggregation extracts:
          - **all_summaries**: ``{agent_id: summary}`` from each agent.
          - **key_themes**: Common topics across agents, derived from
            the first few words of each key point (de-duplicated).
          - **areas_of_agreement**: Key points that appear (or are
            semantically similar) across multiple agents.
          - **areas_of_disagreement**: Conflicting perspectives flagged
            by comparing agent perspectives.
          - **knowledge_gaps**: Stub detection — looks for explicit gap
            language in summaries.

        This is a heuristic / keyword-based approach for Phase 4.
        Phase 5+ may replace it with LLM-powered extraction for higher
        quality and nuance.
        """
        async with self._lock:
            all_summaries = {
                aid: f.summary
                for aid, f in self.round_1_findings.items()
            }

            # Themes: unique first-5-word prefixes from key points.
            theme_counter: Counter[str] = Counter()
            for f in self.round_1_findings.values():
                for kp in f.key_points:
                    prefix = " ".join(kp.split()[:5])
                    if prefix:
                        theme_counter[prefix] += 1

            key_themes = [t for t, _ in theme_counter.most_common(10)]

            # Areas of agreement: key points that multiple agents share
            # (exact substring match across agents).
            all_points: dict[str, set[str]] = {}
            for f in self.round_1_findings.values():
                for kp in f.key_points:
                    normalized = kp.strip().lower()
                    if normalized not in all_points:
                        all_points[normalized] = set()
                    all_points[normalized].add(f.agent_id)

            areas_of_agreement = [
                point
                for point, agents in all_points.items()
                if len(agents) > 1
            ][:10]

            # Areas of disagreement: compare perspective fields for
            # contradictory language markers.
            perspectives = {
                aid: f.perspective.lower()
                for aid, f in self.round_1_findings.items()
            }
            disagreement_markers = [
                "however", "but", "contrary", "disagree", "opposing",
                "limitation", "flaw", "weakness", "contradict",
            ]
            areas_of_disagreement = []
            if len(perspectives) >= 2:
                pids = list(perspectives.keys())
                for i in range(len(pids)):
                    for j in range(i + 1, len(pids)):
                        a, b = pids[i], pids[j]
                        # Simple heuristic: if one mentions limitations
                        # of the other's view, flag as disagreement.
                        combined = perspectives[a] + " " + perspectives[b]
                        if any(m in combined for m in disagreement_markers):
                            areas_of_disagreement.append(
                                f"Tension between '{a}' and '{b}' perspectives"
                            )

            # Knowledge gaps: look for gap-indicating phrases in summaries.
            gap_markers = [
                "further research", "unclear", "unknown", "need",
                "gap", "not understood", "further investigation",
                "requires more", "limited evidence", "insufficient",
            ]
            knowledge_gaps_set: set[str] = set()
            for aid, f in self.round_1_findings.items():
                summary_lower = f.summary.lower()
                for marker in gap_markers:
                    if marker in summary_lower:
                        knowledge_gaps_set.add(
                            f"Agent '{aid}' identifies: {marker.capitalize()}"
                        )

            knowledge_gaps = list(knowledge_gaps_set) or [
                "Further research needed for comprehensive understanding"
            ]

            shared = SharedKnowledge(
                round_number=1,
                all_summaries=all_summaries,
                key_themes=key_themes,
                areas_of_agreement=areas_of_agreement,
                areas_of_disagreement=areas_of_disagreement,
                knowledge_gaps=knowledge_gaps,
            )
            self.shared_knowledge = shared
            logger.debug(
                "SharedKnowledge computed: %d themes, %d agreements, "
                "%d disagreements, %d gaps",
                len(key_themes),
                len(areas_of_agreement),
                len(areas_of_disagreement),
                len(knowledge_gaps),
            )
            return shared

    async def get_shared_knowledge(self) -> SharedKnowledge | None:
        """Return the computed ``SharedKnowledge``, or ``None`` if not yet computed."""
        async with self._lock:
            return self.shared_knowledge

    # ------------------------------------------------------------------
    # Follow-up Questions
    # ------------------------------------------------------------------

    async def publish_followup(
        self, agent_id: str, questions: list[str]
    ) -> None:
        """Publish follow-up questions from an agent after reviewing shared knowledge."""
        async with self._lock:
            self.followup_questions[agent_id] = list(questions)
            logger.debug(
                "Follow-up questions published for agent '%s' (%d questions)",
                agent_id,
                len(questions),
            )

    async def get_followup_questions(self, agent_id: str) -> list[str]:
        """Return follow-up questions for a specific agent."""
        async with self._lock:
            return list(self.followup_questions.get(agent_id, []))

    # ------------------------------------------------------------------
    # Round 2
    # ------------------------------------------------------------------

    async def publish_round_2(self, agent_id: str, findings: Findings) -> None:
        """Publish an agent's Round 2 findings."""
        async with self._lock:
            self.round_2_findings[agent_id] = findings
            logger.debug("Round 2 findings published for agent '%s'", agent_id)

    async def publish_round(self, agent_id: str, round_num: int, findings: Findings) -> None:
        """Publish an agent's findings for any round (generic)."""
        async with self._lock:
            self.other_rounds_findings[(agent_id, round_num)] = findings
            logger.debug("Round %d findings published for agent '%s'", round_num, agent_id)

    # ------------------------------------------------------------------
    # Individual Reports
    # ------------------------------------------------------------------

    async def publish_report(
        self, agent_id: str, report: IndividualReport
    ) -> None:
        """Publish an agent's final individual report."""
        async with self._lock:
            self.individual_reports[agent_id] = report
            logger.debug("Report published for agent '%s'", agent_id)

    async def get_all_reports(self) -> dict[str, IndividualReport]:
        """Return all individual reports (shared-all-reads)."""
        async with self._lock:
            return dict(self.individual_reports)

    # ------------------------------------------------------------------
    # Clarifications
    # ------------------------------------------------------------------

    async def add_clarification(
        self,
        query: ClarificationQuery,
        response: ClarificationResponse,
    ) -> None:
        """Record a clarification query/response pair."""
        async with self._lock:
            self.clarifications.append(
                {
                    "agent_id": query.agent_id,
                    "query": query,
                    "response": response,
                }
            )
            logger.debug(
                "Clarification recorded for agent '%s'", query.agent_id
            )

    async def get_clarifications(self) -> list[dict[str, Any]]:
        """Return all recorded clarification pairs."""
        async with self._lock:
            return list(self.clarifications)
