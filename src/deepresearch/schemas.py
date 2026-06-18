"""JSON schemas for structured agent output.

These schemas guarantee JSON validity when passed to generate_with_tools()
via the response_schema parameter.
"""

ROUND_1_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Concise summary of findings"},
        "key_points": {"type": "array", "items": {"type": "string"}, "description": "Key insights"},
        "perspective": {"type": "string", "description": "Unique perspective on topic"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "Confidence level"}
    },
    "required": ["summary", "key_points", "perspective", "confidence"],
    "additionalProperties": False
}

ROUND_2_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Refined summary"},
        "key_points": {"type": "array", "items": {"type": "string"}, "description": "Refined points"},
        "perspective": {"type": "string", "description": "Evolved perspective"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
    },
    "required": ["summary", "key_points", "perspective", "confidence"],
    "additionalProperties": False
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}, "description": "Follow-up questions"},
        "target_agent_ids": {"type": "array", "items": {"type": "string"}, "description": "Target agent IDs"}
    },
    "required": ["questions", "target_agent_ids"],
    "additionalProperties": False
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "analysis": {"type": "string"},
        "key_insights": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["title", "summary", "analysis", "key_insights"],
    "additionalProperties": False
}