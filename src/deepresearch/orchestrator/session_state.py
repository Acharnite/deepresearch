"""Session state tracking and convergence detection for research sessions."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from deepresearch.constants import MAX_SESSION_DURATION
from deepresearch.models import ResearchTopic, SharedKnowledge

logger = logging.getLogger(__name__)


class SessionState:
    """Tracks session state, round history, and convergence detection.

    Provides methods for gap analysis, diminishing returns detection,
    confidence convergence, and persistence of round findings.
    """

    def __init__(self, session_id: str, topic: ResearchTopic | None = None) -> None:
        self.session_id = session_id
        self.topic = topic
        self.gap_history: list[int] = []
        self.findings_history: list[str] = []
        self.current_round = 0

    # ------------------------------------------------------------------
    # Topic seed for deterministic random assignment
    # ------------------------------------------------------------------

    @property
    def topic_seed(self) -> str:
        """Seed string for deterministic random assignment."""
        if self.topic is not None:
            return self.topic.question
        return "default_seed"

    # ------------------------------------------------------------------
    # Continue / convergence decision
    # ------------------------------------------------------------------

    async def should_continue(
        self,
        cancel_event: Any,
        session_config: Any,
        round_num: int,
        round_history: list[SharedKnowledge],
        start_time: float,
    ) -> bool:
        """Evaluate whether to continue with another research round.

        Priority order:
        1. Cancel event — user-initiated cancellation
        2. Emergency timeout (30 min absolute max — safety net only)
        3. Max rounds — hard safety cap
        4. Trend convergence — gaps no longer decreasing
        5. Diminishing returns — 2 consecutive non-decreasing gap deltas
        6. Confidence convergence
        """
        # 1. Cancel event
        if cancel_event and cancel_event.is_set():
            logger.info("Cancel event set — stopping rounds")
            return False

        # 2. Emergency timeout (30 min absolute max — safety net only)
        if time.monotonic() - start_time > MAX_SESSION_DURATION:
            logger.warning("Emergency timeout (30 min) reached — stopping")
            return False

        # 3. Max rounds — hard safety cap
        if session_config is not None:
            max_r = (
                session_config.budget.max_rounds
                if hasattr(session_config, 'budget')
                else session_config.max_rounds
            )
            if round_num >= max_r:
                logger.info(
                    "Max rounds reached (%d) — stopping", max_r
                )
                return False

        # 4. Trend convergence — gaps no longer decreasing
        gaps = self.compute_gap_delta(round_history)
        if gaps is not None and gaps >= 0:
            logger.info("Gap delta %.2f >= 0 — convergence detected, stopping", gaps)
            return False

        # 5. Diminishing returns — 2 consecutive non-decreasing gap deltas
        if self.diminishing_returns(round_history):
            logger.info("Diminishing returns detected — stopping")
            return False

        # 6. Confidence convergence
        if self.converged_by_confidence(round_history):
            logger.info("Confidence convergence detected — stopping")
            return False

        return True

    # ------------------------------------------------------------------
    # Gap / convergence helpers (stateless)
    # ------------------------------------------------------------------

    @staticmethod
    def total_gaps(shared: SharedKnowledge) -> int:
        """Count total gaps (knowledge_gaps + disagreements)."""
        return len(shared.knowledge_gaps) + len(shared.areas_of_disagreement)

    @staticmethod
    def compute_gap_delta(
        round_history: list[SharedKnowledge],
    ) -> float:
        """Compute gap delta between last two rounds.

        Positive value = gaps decreasing (progress).
        Negative or zero = stagnation (should stop).

        Requires 2 consecutive rounds of non-decreasing gaps to trigger.
        Returns -1.0 if not enough data to decide.
        """
        if len(round_history) < 3:
            return -1.0  # Not enough data, continue

        d1 = SessionState.total_gaps(round_history[-2]) - SessionState.total_gaps(
            round_history[-1]
        )
        d2 = SessionState.total_gaps(round_history[-3]) - SessionState.total_gaps(
            round_history[-2]
        )
        # Only stop if 2 consecutive non-decreasing gap deltas
        return d1 if d1 <= 0 and d2 <= 0 else -1.0

    @staticmethod
    def diminishing_returns(
        round_history: list[SharedKnowledge],
    ) -> bool:
        """Detect diminishing returns: 2 consecutive non-decreasing gap deltas."""
        if len(round_history) < 3:
            return False

        d1 = SessionState.total_gaps(round_history[-2]) - SessionState.total_gaps(
            round_history[-1]
        )
        d2 = SessionState.total_gaps(round_history[-3]) - SessionState.total_gaps(
            round_history[-2]
        )
        return d1 <= 0 and d2 <= 0

    @staticmethod
    def converged_by_confidence(
        round_history: list[SharedKnowledge],
    ) -> bool:
        """Check if confidence has converged across agents.

        Returns True when mean confidence >= 0.7 for 2+ rounds.
        Note: We don't track per-agent confidence in SharedKnowledge,
        so this is a stub that returns False for now.
        The convergence is detected via gap delta instead.
        """
        return False  # Stub — confidence tracking requires per-agent data in SharedKnowledge

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_round_findings(
        self,
        results: dict[str, Any],
        output_path: Path,
        round_num: int,
    ) -> None:
        """Save round findings to JSON files for reuse."""
        try:
            agents_dir = output_path.parent / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            for agent_id, findings in results.items():
                if findings is None:
                    continue
                agent_file = agents_dir / f"{agent_id}_round{round_num}.json"
                agent_file.write_text(
                    json.dumps(
                        {
                            "agent_id": getattr(findings, "agent_id", agent_id),
                            "round": round_num,
                            "summary": getattr(findings, "summary", ""),
                            "key_points": getattr(findings, "key_points", []),
                            "perspective": getattr(findings, "perspective", ""),
                            "confidence": getattr(findings, "confidence", 0.5),
                            "raw_response": getattr(findings, "raw_response", None),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            logger.info(
                "Saved %d round %d findings to %s",
                len(results),
                round_num,
                agents_dir,
            )
        except Exception as e:
            logger.warning("Failed to save round %d findings: %s", round_num, e)
