"""Legal Case Intelligence Suite.

A modular, multi-agent legal-reasoning platform layered on top of VerdictAI's
existing RAG pipeline, citation system, and judge model. Each agent lives in
its own module and is independently testable (all accept injected llm/retriever
so the suite runs offline with fakes).

Pipeline:
    Case Analyzer -> Evidence Analyzer -> Legal Research (RAG) ->
    Prosecutor -> Defense -> Judge -> Verdict, with a Timeline generator.

Everything here is educational/analytical only (see common.DISCLAIMER and the
safety preamble applied to every agent prompt).
"""

from .orchestrator import run_case_intelligence
from .demo_cases import DEMO_CASES
from .common import DISCLAIMER, VERDICTS, STRENGTHS

__all__ = ["run_case_intelligence", "DEMO_CASES", "DISCLAIMER", "VERDICTS", "STRENGTHS"]
