# ADR-0015: Fix JSON Parsing and Topic Drift

## Status

Proposed

## Version

1.0

## Last Updated

2026-06-17

## Context

Two root causes degrade DeepeResearch output quality across all agents and all later stages:

1. **JSON Parse Errors** — The `_strip_tool_output()` approach in the ADR targets `[🔍 Web Search]` patterns, but in the streaming path these go to UI panels, NOT into `full_text`. The actual bug is in the **non-streaming fallback** in `generate_with_tools()`: when streaming fails, `full_text += text_content` accumulates across tool rounds WITHOUT resetting `_round_text`. The existing `if not tool_calls` check only overwrites `full_text` with `_round_text`, but `_round_text` is never set in the non-streaming path, so stale accumulated text leaks into the final JSON response.

   Affects: `refine_findings`, `clarify`, `research_round_3` — all stages that use `generate_with_tools()`. Round 1 also fails but has a retry-without-tools fallback. Note: `write_report` does NOT use `generate_with_tools()` and is unaffected.

2. **Topic Not in Scribe Compile Prompt** — The `compile()` method (scribe_agent.py line 171) builds a user_prompt without including the `topic` parameter. The topic is passed to `compile()` but only used in the clarification protocol. The Scribe system prompt also omits it. Result: the Scribe has no idea what the paper should be about and produces topic-drifted output.

## Decision

### Fix 1: Fix Non-Streaming Fallback + Safety Net Strip Tool Output

**Root cause:** In `generate_with_tools()`, when streaming fails, the non-streaming fallback sets `full_text += text_content` but never sets `_round_text = text_content`. The existing `if not tool_calls` check overwrites `full_text` with `_round_text`, but `_round_text` is empty, so stale text accumulates across rounds.

**Fix:** Change `full_text += text_content` to `_round_text = text_content` in the non-streaming fallback path. Add `_strip_tool_output()` as a safety net called in `parse_json_response()`.```python
# In generate_with_tools() non-streaming fallback (~line 618):
# WRONG (current):
full_text += text_content
# RIGHT (fix):
_round_text = text_content

# New static method on LLMClient:
@staticmethod
def _strip_tool_output(response: str) -> str:
    """Remove tool-related prefixes and output from LLM response text."""
    import re
    cleaned = re.sub(
        r'\[[^\]]*\]\s*Query:.*?(?=\n\n|\n[^ []|$)', '', response, flags=re.DOTALL
    )
    cleaned = re.sub(r'^\s*[•\-]\s+.*$', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

# In parse_json_response(), strip tool output BEFORE parsing:
def parse_json_response(self, response: str) -> dict[str, Any]:
    response = self._strip_tool_output(response)  # safety net
    # ... existing parsing logic unchanged ...
```

### Fix 2: Include Topic in Scribe Compile Prompt

Update `compile()` in `scribe_agent.py` (line 171) to include the topic in the user_prompt:

```python
user_prompt = (
    f"# Compile Research Paper\n\n"
    f"**Research Topic: {topic}**\n\n"
    f"The following are individual reports from {len(reports)} "
    f"research agents on the topic above. Synthesise them into a coherent paper "
    f"about this specific topic.\n\n"
    f"**CRITICAL: The paper MUST be about \"{topic}\". All content — abstract, "
    f"synthesis, key takeaways, conclusion — must directly address this topic.**\n\n"
    f"**IMPORTANT: Use EXACTLY these agent names for section headings: {agent_names}**\n"
    f"**Do NOT invent new agent names, titles, or perspective names.**\n\n"
    f"{reports_text}\n\n"
    "Use the EXACT agent names from the reports above. "
    "Each agent section must be titled with the agent's real name. "
    "Highlight areas of agreement and disagreement."
)
```

### Fix 3: Add Topic to Scribe System Prompt

Add the topic to the system_prompt build section (after line 169, before line 171):

```python
if topic:
    system_prompt += (
        f"\n\n**The paper you are compiling is about: {topic}**"
        f"\nAll content must stay strictly on this topic."
    )
```

## Consequences

### Positive

- Agents' actual JSON responses are no longer polluted by tool output text
- Scribe always knows the topic and produces topic-focused papers
- No more "open-ended" descriptions when topic was provided
- Agents that fail JSON parse get empty `{}` fallback — existing behavior, now less frequent

### Negative

- Tool output stripping is regex-based — may miss edge cases
- If the LLM intentionally discusses search results in JSON context, stripping may remove valid content (unlikely in practice)

## References

- `src/deepresearch/llm/client.py` — `parse_json_response()` at line 747
- `src/deepresearch/agents/scribe_agent.py` — `compile()` at line 171
- ADR-0006: Web Search and Tool Calling
- ADR-0007: Clarification Protocol and Refinement
- ADR-0014: Enforce Time Budget and Correct Labels
