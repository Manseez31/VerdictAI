"""Arena (Act) routing logic shared by backend.py and rag_pipeline.py.

Kept dependency-light (no ML/vector-store imports) so routing decisions can
be unit tested without paying the cost of loading embedding models, Chroma,
or hitting the Groq API.
"""

from __future__ import annotations

from typing import Callable, Optional

# Single source of truth: which keywords route a query to which arena, and
# which PDF source file(s) back that arena. Order matters for decide_arena's
# rule matching below (first match wins).
ARENA_KEYWORDS: dict[str, list[str]] = {
    "Single Women Act": ["single women", "single woman", "एकल महिला", "ekal mahila", "विधवा"],
    "Pharmacy Act": ["pharmacy", "pharmasi", "फार्मेसी"],
    "Immunization Act": ["immunization", "khop", "खोप", "इम्युनाइजेशन"],
    "Sports Act": ["sports", "sport", "खेलकुद", "खेल"],
    "Constitution of Nepal": ["citizenship", "nagrita", "नागरिकता", "संविधान", "constitution"],
}

CATEGORY_TO_SOURCES: dict[str, Optional[list[str]]] = {
    "All (auto)": None,
    "Pharmacy Act": ["pharmacy.pdf"],
    "Immunization Act": ["immunization.pdf"],
    "Constitution of Nepal": ["constitution.pdf"],
    "Single Women Act": ["single_women.pdf"],
    "Sports Act": ["sports.pdf"],
}

GREETINGS = [
    "hi", "hello", "hey", "namaste", "namastey", "नमस्ते",
    "hi there", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "dhanyabad", "धन्यवाद",
]


def is_generic_greeting(text: str) -> bool:
    t = text.strip().lower()
    return len(t) <= 15 and any(t == g for g in GREETINGS)


def match_arena_by_keyword(text: str) -> Optional[str]:
    """Return the first arena whose keywords appear in `text`, or None."""
    lowered = text.lower()
    for arena, keywords in ARENA_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return arena
    return None


def decide_arena(
    message: str,
    arena_from_ui: str,
    classify_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """Decide which arena (Act) a chat message belongs to.

    Priority: explicit UI selection > greeting/empty short-circuit >
    keyword rules > ML classifier fallback (if provided).
    """
    text = message.lower().strip()

    if arena_from_ui != "All (auto)":
        return arena_from_ui

    if is_generic_greeting(text) or not text:
        return "All (auto)"

    matched = match_arena_by_keyword(text)
    if matched is not None:
        return matched

    if classify_fn is not None:
        return classify_fn(message)

    return "All (auto)"


def build_where_for_category(category: str) -> Optional[dict]:
    sources = CATEGORY_TO_SOURCES.get(category)
    if not sources:
        return None
    if len(sources) == 1:
        return {"source_file": sources[0]}
    return {"$or": [{"source_file": s} for s in sources]}


def choose_where(query: str, norm_query: str) -> Optional[dict]:
    """Decide which PDF to search based on keywords in the query text."""
    arena = match_arena_by_keyword(query + " " + norm_query)
    if arena is None:
        return None
    return build_where_for_category(arena)
