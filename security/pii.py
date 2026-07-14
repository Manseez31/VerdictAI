"""PII detection and redaction.

WHY THIS EXISTS
---------------
Case documents and chat messages routinely contain personal data (citizenship
numbers, PAN, phone numbers, bank accounts). That data currently flows to a
third-party LLM API and into local audit/eval logs. Redacting it before it
leaves the process reduces both third-party exposure and the blast radius of a
log leak.

Design note: redaction is applied to what we LOG and (optionally) to what we
send to the model — not silently to what the user sees, because destroying the
identifiers inside a legal document would corrupt the analysis. Callers choose.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Ordered: more specific patterns first so they win over generic number matches.
_PII_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    # Nepali citizenship: e.g. 12-34-56-78901 or 123456789
    ("CITIZENSHIP_NO", re.compile(r"\b\d{2}-\d{2}-\d{2}-\d{4,6}\b")),
    # Nepali PAN: 9 digits
    ("PAN", re.compile(r"\bPAN[:\s#]*\d{9}\b", re.IGNORECASE)),
    ("BANK_ACCOUNT", re.compile(r"\b(?:a/c|account)[:\s#no.]*\d{8,20}\b", re.IGNORECASE)),
    # Nepali mobile (+977 98XXXXXXXX) and generic long phone numbers
    ("PHONE", re.compile(r"(?:\+977[-\s]?)?\b9[678]\d{8}\b")),
    ("IP_ADDRESS", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # Crypto wallet (case files in this project include crypto scams)
    ("CRYPTO_WALLET", re.compile(r"\b0x[a-fA-F0-9]{40}\b")),
    ("API_KEY", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
]


def detect_pii(text: str) -> Dict[str, List[str]]:
    """Return {pii_type: [matches]} found in `text`."""
    found: Dict[str, List[str]] = {}
    for label, rx in _PII_PATTERNS:
        hits = rx.findall(text or "")
        if hits:
            found[label] = [h if isinstance(h, str) else str(h) for h in hits][:20]
    return found


def redact_pii(text: str) -> Tuple[str, Dict[str, int]]:
    """Replace PII with typed placeholders.

    Returns (redacted_text, {pii_type: count}). Placeholders keep the text
    readable and analyzable (`[REDACTED_PHONE]`) rather than destroying
    structure.
    """
    if not text:
        return "", {}

    counts: Dict[str, int] = {}
    redacted = text
    for label, rx in _PII_PATTERNS:
        def _sub(_m, _label=label):
            counts[_label] = counts.get(_label, 0) + 1
            return f"[REDACTED_{_label}]"
        redacted = rx.sub(_sub, redacted)

    if counts:
        logger.info("PII redacted: %s", counts)
    return redacted, counts
