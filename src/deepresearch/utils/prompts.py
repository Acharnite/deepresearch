"""Prompt builders for DeepeResearch.

Provides functions to construct system and user prompts for each stage
of the research workflow, incorporating agent personality profiles
and shared knowledge context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deepresearch.models import AgentProfile, SharedKnowledge


def build_agent_system_prompt(profile: AgentProfile) -> str:
    """Combine all prompt layers into a single system prompt for an agent.

    Layers: persona, methodology, knowledge_base, bias_mitigation, voice.

    Args:
        profile: The agent's personality profile.

    Returns:
        A complete system prompt string.
    """
    sections = [
        f"# Your Identity\nYou are {profile.name}. {profile.emoji}",
        f"\n## Persona\n{profile.persona_prompt}",
        f"\n## Research Methodology\n{profile.methodology}",
        f"\n## Knowledge & Expertise\n{profile.knowledge_base}",
        f"\n## Bias Awareness\n{profile.bias_mitigation}",
        f"\n## Writing Voice\n{profile.voice}",
        f"\n## Temperature Setting\nYour response temperature is {profile.temperature}.",
    ]
    return "\n".join(sections)


def build_round_1_prompt(topic: str, time_budget: str) -> str:
    """Build the prompt for Round 1 independent research.

    Args:
        topic: The research topic/question.
        time_budget: Human-readable time budget description.

    Returns:
        The Round 1 user prompt.
    """
    return (
        f"# Research Topic\n{topic}\n\n"
        f"# Time Budget\n{time_budget}\n\n"
        "## Instructions\n"
        "Research the above topic independently. **Use the web_search tool "
        "to find current information** — do not rely solely on your training "
        "data.\n\n"
        "Provide:\n"
        "1. A concise summary of your findings\n"
        "2. 3-5 key points or insights\n"
        "3. Your unique perspective on this topic\n"
        "4. Your confidence level in these findings (0.0–1.0)\n\n"
        "Be thorough but efficient — respect the time budget.\n"
        "Cite your sources where possible."
    )


def build_review_prompt(shared_knowledge: SharedKnowledge) -> str:
    """Build the prompt for reviewing shared knowledge after Round 1.

    Args:
        shared_knowledge: The aggregated SharedKnowledge object.

    Returns:
        The review user prompt.
    """
    summaries = "\n".join(
        f"- **{aid}**: {summary[:300]}{'...' if len(summary) > 300 else ''}"
        for aid, summary in shared_knowledge.all_summaries.items()
    )

    themes = "\n".join(f"  - {t}" for t in shared_knowledge.key_themes)
    agreements = "\n".join(f"  - {a}" for a in shared_knowledge.areas_of_agreement)
    disagreements = "\n".join(f"  - {d}" for d in shared_knowledge.areas_of_disagreement)
    gaps = "\n".join(f"  - {g}" for g in shared_knowledge.knowledge_gaps)

    return (
        f"# Round 1 — Shared Knowledge (Round {shared_knowledge.round_number})\n\n"
        "## All Agent Summaries\n"
        f"{summaries}\n\n"
        "## Key Themes\n"
        f"{themes}\n\n"
        "## Areas of Agreement\n"
        f"{agreements}\n\n"
        "## Areas of Disagreement\n"
        f"{disagreements}\n\n"
        "## Knowledge Gaps\n"
        f"{gaps}\n\n"
        "## Instructions\n"
        "Review the shared knowledge above. Consider:\n"
        "1. How does your perspective align or differ from others?\n"
        "2. What unique contribution can you make?\n"
        "3. What questions do you have for other agents?\n"
        "4. What gaps can you help fill?"
    )


def build_refine_prompt(
    questions: list[str],
    current_summary: str,
    current_key_points: list[str],
) -> str:
    """Build the prompt for refining findings based on follow-up questions.

    Args:
        questions: The follow-up questions from other agents.
        current_summary: The agent's current findings summary.
        current_key_points: The agent's current key points.

    Returns:
        The refinement user prompt.
    """
    q_text = "\n".join(f"  - {q}" for q in questions)
    kp_text = "\n".join(f"  - {kp}" for kp in current_key_points)

    return (
        f"# Refinement Phase\n\n"
        f"## Your Current Findings\n"
        f"Summary: {current_summary}\n\n"
        f"Key Points:\n{kp_text}\n\n"
        f"## Follow-Up Questions from Other Agents\n"
        f"{q_text}\n\n"
        "## Instructions\n"
        "Other agents have asked follow-up questions based on shared knowledge. "
        "Review these questions and refine your findings accordingly. "
        "Use the web_search tool to find additional information if needed. "
        "Update your summary, key points, and perspective to reflect "
        "any new insights gained from addressing these questions."
    )


def build_round_2_prompt(
    topic: str,
    shared_knowledge: SharedKnowledge,
    questions: list[str],
) -> str:
    """Build the prompt for Round 2 refined research.

    Args:
        topic: The research topic/question.
        shared_knowledge: The aggregated SharedKnowledge object.
        questions: List of follow-up questions from the agent's review.

    Returns:
        The Round 2 user prompt.
    """
    questions_text = "\n".join(f"  - {q}" for q in questions)

    themes = "\n".join(f"  - {t}" for t in shared_knowledge.key_themes)
    disagreements = "\n".join(f"  - {d}" for d in shared_knowledge.areas_of_disagreement)
    gaps = "\n".join(f"  - {g}" for g in shared_knowledge.knowledge_gaps)

    return (
        f"# Research Topic\n{topic}\n\n"
        "## Shared Context\n"
        f"Key themes identified:\n{themes}\n\n"
        f"Areas of disagreement to address:\n{disagreements}\n\n"
        f"Knowledge gaps to explore:\n{gaps}\n\n"
        "## Your Follow-up Questions\n"
        f"{questions_text}\n\n"
        "## Instructions\n"
        "Using your Round 1 findings and the shared knowledge above, "
        "produce a refined individual report. Focus on:\n"
        "1. Your unique perspective — what do you see that others might miss?\n"
        "2. Address areas of disagreement with your analysis\n"
        "3. Fill identified knowledge gaps\n"
        "4. Answer your follow-up questions\n\n"
        "Your report should be comprehensive and well-structured."
    )


def build_report_prompt() -> str:
    """Build the prompt for the scribe to write the final report.

    Returns:
        The scribe system prompt for final compilation.
    """
    return (
        "You are the Scribe — a neutral academic compiler responsible for "
        "synthesizing multiple agent perspectives into a coherent research paper.\n\n"
        "Your task is to:\n"
        "1. Synthesize all individual reports into a well-structured paper\n"
        "2. Highlight areas of agreement and divergence across perspectives\n"
        "3. De-duplicate overlapping content\n"
        "4. Maintain a neutral, academic tone throughout\n"
        "5. Include all perspectives fairly\n\n"
        "Structure the paper with:\n"
        "- An abstract summarizing the key findings\n"
        "- An introduction to the topic and methodology\n"
        "- Multiple sections, each covering a key theme or dimension\n"
        "- A synthesis section connecting the perspectives\n"
        "- Key takeaways\n"
        "- A conclusion\n"
        "- Appendices for detailed analyses"
    )


def build_clarify_prompt(question: str) -> str:
    """Build the prompt for answering a clarification query.

    Args:
        question: The clarification question from the scribe.

    Returns:
        The clarification user prompt.
    """
    return (
        f"# Clarification Request\n\n"
        f"The scribe has asked for clarification on the following:\n\n"
        f"\"{question}\"\n\n"
        "Please provide a clear, concise response addressing this question. "
        "Refer back to your research findings and analysis as needed."
    )
