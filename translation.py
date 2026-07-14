"""Nepali → English translation of generated answers.

This module is deliberately a *post-processing* stage: it receives the
already-generated Nepali answer text and nothing else. It never touches
retrieval, citation extraction, or the judge pipeline, and it must not —
translation is based solely on the generated answer (see /translate contract).

Kept dependency-light (no rag_pipeline import) so it can be unit tested
without loading embedding models or the vector store. The translator LLM is
created lazily on first use via the same Groq infrastructure as the rest of
the app.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# A larger model than the 8B generator: legal-register Nepali → English is a
# harder translation task, and this endpoint is low-volume (on-demand clicks).
DEFAULT_TRANSLATION_MODEL = "llama-3.3-70b-versatile"

# Language directions supported by the /translate endpoint. Default direction
# is Nepali -> English (the original behaviour); the Case Intelligence dashboard
# uses English -> Nepali for its bilingual toggle.
_LANGS = {"en": "English", "ne": "Nepali"}

_PROMPT_TEMPLATE = """You are a professional legal translator. Translate the following {source_lang} legal text into {target_lang}.

STRICT RULES:
1. Produce natural, professional legal {target_lang}. Legal terminology must remain accurate — do not simplify or reinterpret legal concepts.
2. Keep every citation tag of the form [स्रोत: ...] EXACTLY as it appears in the source, character for character. Do not translate, alter, move, or remove these tags.
3. Preserve Act names and section numbers exactly. Keep section references consistent (e.g. "Section 21" ↔ "धारा 21") using the numbering system natural to {target_lang}.
4. Preserve the structure of the source: keep the same line breaks, paragraphs, bullet points, numbering, and **bold** markers in the same places.
5. Do not add, remove, soften, or strengthen any legal claim. Do not add commentary, notes, or explanations.
6. Output ONLY the {target_lang} translation — no preamble, no quotes around it.

Source text:
{text}

{target_lang} translation:"""


def build_translation_prompt(text: str, target_lang: str = "en") -> str:
    """Exposed separately so tests can assert on prompt construction."""
    target = _LANGS.get(target_lang, "English")
    source = _LANGS["ne"] if target_lang == "en" else _LANGS["en"]
    return _PROMPT_TEMPLATE.format(text=text, source_lang=source, target_lang=target)


# Lazy process-wide translator LLM (only built if translation is actually used).
_translation_llm = None


def _get_default_llm():
    global _translation_llm
    if _translation_llm is None:
        from dotenv import load_dotenv
        from langchain_groq import ChatGroq

        load_dotenv()
        api_key = os.getenv("GROQ_API_KEY")
        if api_key is None:
            raise ValueError("GROQ_API_KEY not set in environment or .env file")
        model_name = os.getenv("TRANSLATION_MODEL", DEFAULT_TRANSLATION_MODEL)
        logger.info("Initializing translation LLM: %s", model_name)
        _translation_llm = ChatGroq(
            groq_api_key=api_key,
            model_name=model_name,
            temperature=0.0,   # translation should be deterministic and literal
            max_tokens=2048,   # answers can be long; don't truncate mid-translation
        )
    return _translation_llm


def translate_text(text: str, target_lang: str = "en", llm=None) -> str:
    """Translate legal text into `target_lang` ('en' or 'ne').

    Pure text-in/text-out: no retrieval, no citation re-processing (citation
    tags are preserved verbatim by the prompt). Raises ValueError on empty
    input/output so the endpoint can map failures to proper HTTP errors.
    """
    if not text or not text.strip():
        raise ValueError("text must not be empty")
    if target_lang not in _LANGS:
        raise ValueError(f"unsupported target_lang: {target_lang!r}")

    llm = llm or _get_default_llm()
    resp = llm.invoke(build_translation_prompt(text, target_lang))
    translated = (resp.content or "").strip()
    if not translated:
        raise ValueError("translation model returned empty output")
    return translated


def translate_to_english(text: str, llm=None) -> str:
    """Backward-compatible Nepali → English translation (used by /translate's
    original callers)."""
    return translate_text(text, target_lang="en", llm=llm)
