"""Detection and redaction primitives for protected memory content.

This module deliberately has no database or HTTP dependencies.  The server keeps
the redacted representation in ordinary memory tables and stores originals only
in its protected-source vault.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SensitiveMatch:
    kind: str
    start: int
    end: int


# Patterns are intentionally conservative for generic assignments, while the
# vendor formats catch credentials even when they are not labelled.
PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.I)),
    ("api_key", re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("secret_assignment", re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|client[_ -]?secret|secret|password|passwd|pwd)[\"']?\s*[:=]\s*[\"']?([^\s,;\"']{6,})", re.I)),
)


def detect_sensitive(value):
    """Return non-overlapping matches sorted in source order."""
    text = str(value or "")
    found = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            # For labelled assignments redact the label and value together.
            start, end = match.span()
            found.append(SensitiveMatch(kind, start, end))
    found.sort(key=lambda item: (item.start, -(item.end - item.start)))
    selected = []
    cursor = -1
    for item in found:
        if item.start >= cursor:
            selected.append(item)
            cursor = item.end
    return selected


def redact_text(value):
    """Return ``(redacted_text, matches)`` without retaining secret values."""
    text = str(value or "")
    matches = detect_sensitive(text)
    if not matches:
        return text, []
    chunks = []
    cursor = 0
    for match in matches:
        chunks.append(text[cursor:match.start])
        chunks.append("[REDACTED:%s]" % match.kind)
        cursor = match.end
    chunks.append(text[cursor:])
    return "".join(chunks), matches


def sensitivity_types(matches):
    return list(dict.fromkeys(match.kind for match in matches))


def redact_value(value):
    """Recursively redact strings in JSON-like payloads."""
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value
