"""Case Intelligence orchestrator — runs the full multi-agent pipeline.

    Case Analyzer ─┬─► Evidence Analyzer ─┐
                   ├─► Legal Research (RAG)┤
                   └─► Timeline            │
                                           ▼
                            Prosecutor ─► Defense ─► Judge ─► Verdict
                                           ▼
                                     Final Case Report (dict)

Every agent accepts injected `llm`/`retriever`, so the whole suite runs offline
in tests. The default path lazily builds the production LLM and hybrid retriever.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from . import common as c
from .case_analyzer import analyze_case
from .evidence_agent import analyze_evidence
from .research_agent import research_case
from .prosecutor_agent import prosecution_view
from .defense_agent import defense_view
from .judge_agent import judge_case
from .verdict_agent import predict_verdict
from .timeline_agent import build_timeline

logger = logging.getLogger(__name__)


def run_case_intelligence(
    case: Dict[str, str],
    llm=None,
    retriever=None,
) -> Dict[str, Any]:
    """Run the full Case Intelligence Suite on a case.

    `case` must contain: title, description, jurisdiction, case_type.
    Returns the structured multi-agent report consumed by the dashboard.
    """
    llm = llm or c.get_case_llm()

    title = case.get("title", "")
    logger.info("Case Intelligence started: %r", title)

    # Stage 1 — foundational analysis
    analysis = analyze_case(case, llm)
    evidence = analyze_evidence(case, analysis, llm)
    research = research_case(case, analysis, retriever=retriever, llm=llm)
    timeline = build_timeline(case, analysis, llm)

    # Stage 2 — adversarial reasoning (grounded in research citations)
    prosecution = prosecution_view(case, analysis, evidence, research, llm)
    defense = defense_view(case, analysis, evidence, research, prosecution, llm)

    # Stage 3 — adjudication
    judge = judge_case(case, analysis, evidence, research, prosecution, defense, llm)
    verdict = predict_verdict(case, evidence, prosecution, defense, judge, llm)

    logger.info("Case Intelligence finished: outcome=%s", verdict.get("likely_outcome"))

    # The research context is internal (fed to downstream agents); strip the
    # bulky raw text from the client-facing report but keep the citations.
    research_public = {k: v for k, v in research.items() if k != "context"}

    return {
        "case": {
            "title": title,
            "description": case.get("description", ""),
            "jurisdiction": case.get("jurisdiction", ""),
            "case_type": case.get("case_type", ""),
        },
        "analysis": analysis,
        "timeline": timeline,
        "evidence": evidence,
        "research": research_public,
        "prosecution": prosecution,
        "defense": defense,
        "judge": judge,
        "verdict": verdict,
        "disclaimer": c.DISCLAIMER,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": getattr(llm, "model_name", "unknown"),
    }
