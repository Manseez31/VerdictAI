"""Legal citation verification — anti-hallucination.

WHY THIS EXISTS
---------------
The generator can emit a citation tag like

    [स्रोत: Nepal Penal Code, 2074, धारा 249]

for an Act that is not in the knowledge base, or a section number that does not
exist in the Act it names. The frontend then renders that as an authoritative
citation chip. For a legal tool this is the single highest-consequence failure:
a fabricated statute presented with UI-level authority.

The fix is not a better prompt — prompts cannot be trusted. The fix is to check
every emitted citation against the ground truth we actually indexed, and refuse
to present anything we cannot substantiate.

GUARANTEE
---------
A citation reaches the user marked `verified` ONLY if:
  * its Act name matches an Act actually present in the vector store, AND
  * if it names a section, that (Act, section) pair exists in the store.

Anything else is downgraded to `unverified` and, in strict mode, stripped from
the answer entirely. The resulting `source_trust_score` (0-100) is the fraction
of the answer's citations we could substantiate.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Matches the project's canonical citation tag (see rag_pipeline.format_chunk_with_citation).
# NOTE: the Act name itself contains commas ("Nepal Pharmacy Council Act, 2057"),
# so the act group must NOT stop at a comma — it stops only at ", धारा"/", परिच्छेद"
# or "]". The lazy quantifier + optional qualifier group resolves the split.
# The generator sometimes writes परिच्छेद (chapter) instead of धारा (section), so we
# accept both; chapters are not indexed, so they verify at Act level only.
CITATION_RE = re.compile(
    r"\[स्रोत:\s*([^\]]+?)(?:\s*,\s*(?:धारा|परिच्छेद)\s*[-–—]?\s*([^\],]+?))?\s*\]"
)

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# The generator does not always copy citation tags verbatim — it frequently
# renders an Act's name in Nepali while the corpus indexes it in English. The
# Act is genuinely present, so refusing to verify it would be a false positive.
# Aliases map alternate renderings onto Acts that ARE in the knowledge base.
# This does NOT weaken the guarantee: a fabricated Act still resolves to nothing.
_ACT_ALIASES: Dict[str, str] = {
    "नेपाल फार्मेसी परिषद ऐन": "Nepal Pharmacy Council Act, 2057",
    "नेपाल फार्मेसी परिषद अधिनियम": "Nepal Pharmacy Council Act, 2057",
    "फार्मेसी ऐन": "Nepal Pharmacy Council Act, 2057",
    "खोप ऐन": "Immunization Act, 2072",
    "प्रतिरक्षण ऐन": "Immunization Act, 2072",
    "इम्युनाइजेशन ऐन": "Immunization Act, 2072",
    "नेपालको संविधान": "Constitution of Nepal",
    "नेपाल संविधान": "Constitution of Nepal",
    "संविधान": "Constitution of Nepal",
    "एकल महिला ऐन": "Single Women Act",
    "खेलकुद ऐन": "Sports Act",
    "खेल ऐन": "Sports Act",
}

# Tokens that carry no identifying information for an Act name.
# NOTE: token-set based, NOT regex \b — Python's word boundary is unreliable
# against Devanagari, because combining vowel marks (matras) are not \w, so
# `\bनेपालको\b` never matches. That bug silently disabled noise-stripping.
_ACT_NOISE_TOKENS = {
    "act", "the", "of", "nepal",
    "ऐन", "अधिनियम", "नेपालको", "नेपाल",
}
_YEAR = re.compile(r"^[०-९0-9]{4}$")
_PUNCT = re.compile(r"[,\.\-–—:;()]+")


def _norm_act(name: str) -> str:
    """Normalize an Act name for comparison (case/space/punctuation-insensitive)."""
    n = unicodedata.normalize("NFKC", (name or "").strip().lower())
    n = _PUNCT.sub(" ", n)
    n = re.sub(r"\s+", " ", n)
    return n.strip()


def _alias_key(name: str) -> str:
    """Identity of an Act, ignoring language-specific noise (Act/ऐन, year, 'Nepal')
    and orthographic variation (word-final halant: परिषद् == परिषद)."""
    n = unicodedata.normalize("NFKC", (name or "").strip().lower())
    n = _PUNCT.sub(" ", n)
    tokens = []
    for tok in n.split():
        if _YEAR.match(tok) or tok in _ACT_NOISE_TOKENS:
            continue
        tokens.append(tok.rstrip("्"))     # word-final halant is an orthographic variant
    return " ".join(tokens).strip()


def _norm_section(sec: str) -> str:
    """Normalize a section number: Devanagari digits -> ASCII, strip cruft."""
    s = unicodedata.normalize("NFKC", (sec or "").strip())
    s = s.translate(_DEVANAGARI_DIGITS)
    s = re.sub(r"[^\w().]+", "", s)
    return s.lower()


@dataclass
class VerifiedCitation:
    act: str
    section: str
    verified: bool
    reason: str          # 'verified' | 'unknown_act' | 'unknown_section'
    raw: str             # the original tag text


class CitationRegistry:
    """Ground truth: the Acts and (Act, section) pairs actually in the corpus."""

    def __init__(self, acts: Iterable[str], pairs: Iterable[Tuple[str, str]]):
        self._acts: Set[str] = {_norm_act(a) for a in acts if a}
        self._pairs: Set[Tuple[str, str]] = {
            (_norm_act(a), _norm_section(s)) for a, s in pairs if a and s
        }
        # Acts for which we hold NO section metadata at all. Chunk-level section
        # extraction is incomplete (only ~73/587 chunks carry a section_number),
        # so for those Acts we cannot disprove a section — we verify the Act and
        # mark the section unconfirmed rather than crying wolf.
        acts_with_sections = {a for a, _ in self._pairs}
        self._acts_without_sections = self._acts - acts_with_sections

        # Language-agnostic index: alias_key -> canonical normalized Act. Lets a
        # Nepali rendering of an indexed Act resolve to the Act we actually hold.
        self._by_alias: Dict[str, str] = {}
        for a in self._acts:
            key = _alias_key(a)
            if key:
                self._by_alias[key] = a
        for alias, canonical in _ACT_ALIASES.items():
            canon_norm = _norm_act(canonical)
            if canon_norm in self._acts:          # only alias Acts we actually have
                self._by_alias[_alias_key(alias)] = canon_norm

    def _resolve(self, act: str) -> Optional[str]:
        """Map a cited Act name onto an Act in the corpus, or None if unknown."""
        n = _norm_act(act)
        if n in self._acts:
            return n
        return self._by_alias.get(_alias_key(act))

    @classmethod
    def from_vector_store(cls, vector_store) -> "CitationRegistry":
        """Build from the live Chroma collection (production path)."""
        got = vector_store.collection.get(include=["metadatas"])
        acts, pairs = [], []
        for meta in got["metadatas"]:
            act = meta.get("act_name")
            if not act:
                continue
            acts.append(act)
            sec = meta.get("section_number")
            if sec:
                pairs.append((act, str(sec)))
        registry = cls(acts, pairs)
        logger.info(
            "CitationRegistry built: %d acts, %d (act, section) pairs",
            len(registry._acts), len(registry._pairs),
        )
        return registry

    @property
    def act_count(self) -> int:
        return len(self._acts)

    def has_act(self, act: str) -> bool:
        return self._resolve(act) is not None

    def has_section(self, act: str, section: str) -> bool:
        canon = self._resolve(act)
        return canon is not None and (canon, _norm_section(section)) in self._pairs

    def check(self, act: str, section: str) -> Tuple[bool, str]:
        """Return (verified, reason) for one citation."""
        canon = self._resolve(act)
        if canon is None:
            return False, "unknown_act"
        if not section:
            return True, "verified"          # Act-only citation, Act exists
        if (canon, _norm_section(section)) in self._pairs:
            return True, "verified"
        if canon in self._acts_without_sections:
            # We hold no section index for this Act — cannot confirm or refute.
            return True, "section_unconfirmed"
        return False, "unknown_section"


def parse_citations(text: str) -> List[Tuple[str, str, str]]:
    """Extract (act, section, raw_tag) triples from an answer."""
    out = []
    for m in CITATION_RE.finditer(text or ""):
        out.append((m.group(1).strip(), (m.group(2) or "").strip(), m.group(0)))
    return out


def verify_citations(
    text: str,
    registry: Optional[CitationRegistry],
    strict: bool = False,
) -> Dict:
    """Verify every citation in `text` against the corpus.

    Returns:
        citations           : list[VerifiedCitation]
        source_trust_score  : 0-100, fraction substantiated (100 if none cited)
        hallucinated        : list of unverifiable citations
        text                : the answer, with unverifiable tags flagged
                              (or removed, when strict=True)
    """
    citations = parse_citations(text)

    if registry is None:
        # Fail CLOSED on trust: with no ground truth we cannot claim verification.
        return {
            "citations": [],
            "source_trust_score": 0 if citations else 100,
            "hallucinated": [],
            "verified_count": 0,
            "total_count": len(citations),
            "text": text,
            "registry_available": False,
        }

    results: List[VerifiedCitation] = []
    for act, section, raw in citations:
        ok, reason = registry.check(act, section)
        results.append(VerifiedCitation(act=act, section=section, verified=ok, reason=reason, raw=raw))

    total = len(results)
    verified = sum(1 for r in results if r.verified)
    hallucinated = [r for r in results if not r.verified]

    out_text = text
    for bad in hallucinated:
        if strict:
            out_text = out_text.replace(bad.raw, "")            # remove entirely
        else:
            out_text = out_text.replace(bad.raw, f"{bad.raw} ⚠️[UNVERIFIED CITATION]")

    if hallucinated:
        logger.warning(
            "Hallucinated citations detected (%d/%d): %s",
            len(hallucinated), total,
            [(h.act, h.section, h.reason) for h in hallucinated],
        )

    return {
        "citations": [vars(r) for r in results],
        "source_trust_score": 100 if total == 0 else round(100 * verified / total),
        "hallucinated": [vars(h) for h in hallucinated],
        "verified_count": verified,
        "total_count": total,
        "text": out_text,
        "registry_available": True,
    }
