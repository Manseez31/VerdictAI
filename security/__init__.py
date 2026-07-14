"""VerdictAI Security & Trust Core.

Defense-in-depth for an LLM/RAG legal platform. The threat model that actually
matters here is not classic web injection (there is no user-controlled SQL) —
it is *content* attacks against the model:

  1. Prompt injection / jailbreak   -> security.prompt_guard
     Untrusted text (chat messages, and especially UPLOADED DOCUMENTS) flows
     into agent prompts. An attacker's PDF can carry instructions.

  2. Hallucinated legal citations   -> security.citation_verifier
     The model can invent an Act or a section number, which the UI then renders
     as an authoritative citation. For a legal tool this is the highest-
     consequence failure mode, so every citation is checked against the real
     indexed corpus before it reaches the user.

  3. PII leakage                    -> security.pii
  4. Malicious uploads              -> security.files
  5. Unauditable decisions          -> security.audit (hash-chained log)
  6. Browser-side attacks           -> security.headers (CSP, HSTS, etc.)

Every module here is pure/injectable so the whole security layer is testable
offline against a real attack corpus (see tests/test_security.py).
"""

from .prompt_guard import (
    InjectionVerdict,
    scan_for_injection,
    sanitize_untrusted,
    wrap_untrusted,
)
from .citation_verifier import (
    CitationRegistry,
    VerifiedCitation,
    verify_citations,
)
from .pii import redact_pii, detect_pii
from .output_guard import guard_output, OutputVerdict
from .audit import AuditLog
from .files import validate_upload, sha256_hex, UnsafeUpload

__all__ = [
    "InjectionVerdict", "scan_for_injection", "sanitize_untrusted", "wrap_untrusted",
    "CitationRegistry", "VerifiedCitation", "verify_citations",
    "redact_pii", "detect_pii",
    "guard_output", "OutputVerdict",
    "AuditLog",
    "validate_upload", "sha256_hex", "UnsafeUpload",
]
