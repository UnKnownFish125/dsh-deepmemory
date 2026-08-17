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
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _luhn_ok(digits):
    """True when the digit string passes the Luhn checksum."""
    if not digits.isdigit():
        return False
    total = 0
    for index, ch in enumerate(reversed(digits)):
        d = int(ch)
        if index % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _id_card_ok(value):
    """True when the 18-digit mainland ID card passes the GB 11643 check digit."""
    if len(value) != 18 or not value[:17].isdigit():
        return False
    total = sum(int(value[i]) * _ID_WEIGHTS[i] for i in range(17))
    return _ID_CHECK[total % 11] == value[17].upper()


# Patterns are intentionally conservative for generic assignments, while the
# vendor formats catch credentials even when they are not labelled.  PII
# patterns (bank card / ID card) are gated by checksum validation in
# detect_sensitive so that long runs of digits in prose are not redacted.
PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.I)),
    ("api_key", re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("secret_assignment", re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|client[_ -]?secret|secret|password|passwd|pwd)[\"']?\s*[:=]\s*[\"']?([^\s,;\"']{6,})", re.I)),
    ("bank_card", re.compile(r"(?<!\d)\d{13,19}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)[1-9]\d{16}[\dXx](?!\d)")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("natural_password", re.compile(r"(?:密码|口令|passwd|password|pwd)\s*(?:是|为|[:=])?\s*([A-Za-z0-9@#_!?~.*-]{6,32})", re.I)),
)


def detect_sensitive(value):
    """Return non-overlapping matches sorted in source order."""
    text = str(value or "")
    found = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            if kind == "bank_card" and not _luhn_ok(text[match.start():match.end()]):
                continue
            if kind == "id_card" and not _id_card_ok(text[match.start():match.end()]):
                continue
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
