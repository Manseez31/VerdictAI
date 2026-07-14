"""Query rewriting layer.

Rewrites a user question into a retrieval-optimized query *before* search.
Distinct from ``rag_pipeline.expand_query_with_llm`` (which is tuned for
answering): this stage optimizes purely for lexical + dense recall by
surfacing the statutory terminology a matching legal chunk is likely to
contain, while preserving the user's original intent and scope.

Configurable via ``RETRIEVAL_CONFIG.enable_query_rewriting``. When disabled,
``rewrite_query_for_retrieval`` is a no-op that returns the input unchanged.
Both the original and rewritten query are logged for observability.
"""

from __future__ import annotations

import logging

from retrieval_config import RETRIEVAL_CONFIG, RetrievalConfig

logger = logging.getLogger(__name__)

_REWRITE_PROMPT = """तपाईं नेपाली कानुनी खोज (legal search) का लागि प्रश्न पुनर्लेखन गर्ने सहायक हुनुहुन्छ।

लक्ष्य: तलको प्रश्नलाई यस्तो रूपमा पुनर्लेखन गर्नुहोस् जसले सम्बन्धित कानुनी दस्तावेज (ऐन/धारा) खोज्न सजिलो होस्।

कडा नियम:
- प्रयोगकर्ताको मूल आशय (intent), विषय र कानुन नबदल्नुहोस्।
- नयाँ विषय वा नयाँ कानुन नथप्नुहोस् (topic drift निषेध)।
- सम्भव भएमा सान्दर्भिक कानुनी शब्दावली/पर्यायवाची थप्नुहोस् (जस्तै: "दर्ता" सँगै "प्रमाणपत्र", "इजाजतपत्र"; "खोप" सँगै "प्रतिरक्षण"; "अधिकार" सँगै "मौलिक हक")।
- रोमन नेपाली/अङ्ग्रेजीमा लेखिएको भए देवनागरीमा रूपान्तरण गर्नुहोस्।
- केवल एउटै लाइनमा पुनर्लेखित खोज-प्रश्न मात्र आउटपुट गर्नुहोस्; कुनै व्याख्या, नम्बर वा उद्धरण चिन्ह नराख्नुहोस्।

मूल प्रश्न:
{query}

पुनर्लेखित खोज-प्रश्न (देवनागरीमा, केवल एक लाइन):"""


def rewrite_query_for_retrieval(
    query: str,
    llm,
    config: RetrievalConfig = RETRIEVAL_CONFIG,
) -> str:
    """Return a retrieval-optimized rewrite of `query`.

    Falls back to the original query when rewriting is disabled or the LLM
    returns something empty/degenerate, so it can never make retrieval worse
    by dropping the query.
    """
    if not config.enable_query_rewriting:
        return query

    try:
        resp = llm.invoke(_REWRITE_PROMPT.format(query=query))
        rewritten = (resp.content or "").strip()
    except Exception:
        logger.exception("Query rewriting failed; falling back to original query")
        return query

    # Guard against the model returning nothing useful.
    if not rewritten or len(rewritten) < 2:
        logger.warning("Query rewriting produced empty output; using original query")
        return query

    logger.info("[query-rewrite] original=%r rewritten=%r", query, rewritten)
    return rewritten
