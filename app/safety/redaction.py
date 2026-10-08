"""Redaction of secrets and payment data from logs, traces and LLM input."""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = re.compile(r"(api[_-]?key|token|secret|password|authorization|card|cvv|iban|account)", re.I)
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),          # OpenAI style keys
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I),
]
CARD_PATTERN = re.compile(r"\b(?:\d{4}[ -]){3}\d{4}\b|\b\d{4}[ -]\d{6}[ -]\d{5}\b|\b\d{13,19}\b")
CVV_PATTERN = re.compile(r"\b(cvv|cvc)\s*[:=]?\s*\d{3,4}\b", re.I)


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def redact_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    for p in SECRET_PATTERNS:
        text = p.sub("[REDACTED_SECRET]", text)
    text = CARD_PATTERN.sub(lambda m: "[REDACTED_CARD]" if luhn_valid(m.group(0)) else m.group(0), text)
    text = CVV_PATTERN.sub("[REDACTED_CVV]", text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact a JSON-like structure."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and SENSITIVE_KEYS.search(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def sanitise_user_request(text: str) -> tuple[str, bool]:
    """Remove payment credentials before the text reaches the LLM. Returns (clean_text, changed)."""
    clean = redact_text(text)
    return clean, clean != text
