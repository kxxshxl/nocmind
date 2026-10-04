from __future__ import annotations

import re
from typing import Any

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(the\s+)?system\s+prompt",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"disable\s+(all\s+)?safety",
    r"bypass\s+(all\s+)?guardrails",
    r"execute\s+arbitrary\s+(command|code)",
    r"you are now\s+an?\s+admin",
    r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|context)",
    r"forget\s+(all\s+)?(previous|prior|your)\s+instructions",
    r"new\s+instructions\s*:",
    r"(call|invoke|execute|run)\s+(the\s+)?(restart_service|restart_dns|redistribute_traffic|scale_service|create_ticket)",
    r"approve\s+(the\s+)?remediation\s+automatically",
    r"<\s*/?\s*(system|assistant)\s*>",
]


def contains_prompt_injection(text: str) -> bool:
    lower = text.lower()
    return any(re.search(pattern, lower) for pattern in INJECTION_PATTERNS)


def sanitize_user_text(text: str) -> tuple[str, bool]:
    if not contains_prompt_injection(text):
        return text, False
    clean = text
    for pattern in INJECTION_PATTERNS:
        clean = re.sub(pattern, "[BLOCKED_INSTRUCTION]", clean, flags=re.I)
    return clean, True


def sanitize_untrusted(value: Any) -> tuple[Any, bool]:
    """Recursively neutralise instruction-like text in tool results / retrieved documents.

    Retrieved content is data, not instructions. This pattern filter is a defence-in-depth layer on top of
    the system prompts and the hard controls (read-only tool allowlist, human approval for remediation).
    """
    if isinstance(value, str):
        return sanitize_user_text(value)
    if isinstance(value, dict):
        flagged = False
        out = {}
        for k, v in value.items():
            out[k], f = sanitize_untrusted(v)
            flagged = flagged or f
        return out, flagged
    if isinstance(value, list):
        flagged = False
        items = []
        for v in value:
            item, f = sanitize_untrusted(v)
            items.append(item)
            flagged = flagged or f
        return items, flagged
    return value, False


def validate_grounded_answer(answer: str, evidence: list[dict]) -> tuple[bool, float]:
    """Simple deterministic grounding check used as a safety signal, not a substitute for human review."""
    important = {x for x in re.findall(r"[a-z0-9]+", answer.lower()) if len(x) > 3}
    evidence_words = {x for x in re.findall(r"[a-z0-9]+", " ".join(str(e.get('text', '')) for e in evidence).lower()) if len(x) > 3}
    overlap = len(important & evidence_words) / max(len(important), 1)
    return overlap >= 0.2, min(1.0, overlap)
