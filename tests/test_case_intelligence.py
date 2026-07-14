"""Offline tests for the Legal Case Intelligence Suite.

The LLM and the RAG retriever are both faked, so the whole multi-agent pipeline
(including the "RAG" research agent) runs without models, network, or the vector
store.
"""

import json
from types import SimpleNamespace

from case_intelligence import run_case_intelligence, DEMO_CASES
from case_intelligence.common import extract_json
from case_intelligence.research_agent import research_case, build_research_queries
from case_intelligence.documents import extract_document_text, UnsupportedDocument
import pytest


CASE = {
    "title": "Fake Investment Scheme",
    "description": "A promised 20% monthly returns, collected 5M NPR, paid old investors with new deposits until it collapsed. Bank records and 12 investor testimonies exist.",
    "jurisdiction": "Nepal",
    "case_type": "Fraud",
}


class ScriptedLLM:
    """Returns a fixed JSON reply for every agent call (fields are a superset)."""

    def __init__(self, reply=None):
        self.reply = reply or json.dumps(_SUPERSET)
        self.calls = 0
        self.prompts = []

    def invoke(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.reply)


class FakeRetriever:
    """Mimics rag_pipeline.hybrid_retriever.retrieve() with canned chunks."""

    def __init__(self, chunks=None):
        self.chunks = chunks if chunks is not None else [
            {"id": "c1", "content": "फार्मेसी दर्ता सम्बन्धी व्यवस्था", "metadata": {"act_name": "Nepal Pharmacy Council Act, 2057", "section_number": "3", "source_file": "pharmacy.pdf"}},
            {"id": "c2", "content": "ठगी सम्बन्धी कानुनी व्यवस्था", "metadata": {"act_name": "Constitution of Nepal", "section_number": "", "source_file": "constitution.pdf"}},
        ]
        self.queries = []

    def retrieve(self, query, top_k=5, where=None):
        self.queries.append(query)
        return self.chunks[:top_k]


_SUPERSET = {
    "summary": "A collected money via a Ponzi structure.",
    "parties": [{"name": "A", "role": "accused"}],
    "dates": [{"date": "Jan 12", "event": "agreement signed"}],
    "locations": ["Kathmandu"],
    "alleged_actions": ["operated a Ponzi scheme"],
    "evidence": ["bank records", "investor testimony"],
    "legal_issues": ["Was there intent to defraud?"],
    "possible_charges": ["Fraud"],
    "case_category": "Fraud",
    "items": [{"evidence": "bank records", "classification": "Strong", "reasoning": "official", "confidence": 85}],
    "missing_evidence": ["signed intent to deceive"],
    "witness_reliability": "consistent",
    "document_quality": "high",
    "digital_evidence_quality": "n/a",
    "chain_of_custody_concerns": [],
    "overall_strength": "Strong",
    "applicable_laws": [{"act": "Constitution of Nepal", "section": "", "provision": "x", "relevance": "y"}],
    "research_summary": "Fraud provisions apply [स्रोत: Constitution of Nepal].",
    "arguments": ["The scheme was deceptive"],
    "evidence_based_arguments": ["bank records show circular payments"],
    "legal_references": ["[स्रोत: Constitution of Nepal]"],
    "aggravating_factors": ["large number of victims"],
    "why_charges_apply": "deception for gain",
    "prosecution_weaknesses": ["intent unproven"],
    "evidentiary_concerns": ["how records were obtained"],
    "alternative_interpretations": ["a failed business"],
    "mitigating_circumstances": ["first offence"],
    "legal_reasoning": "The elements appear supported but intent is contested.",
    "findings": ["deception occurred"],
    "prosecution_assessment": "strong",
    "defense_assessment": "moderate",
    "evidence_quality": "Strong",
    "citation_quality": "well grounded",
    "likely_outcome": "Likely Conviction",
    "rationale": "strong documentary evidence",
    "uncertainty_analysis": "intent is the key unknown",
    "key_factors": ["circular payments"],
    "events": [{"date": "Jan 12", "event": "agreement signed", "significance": "start"}],
    "reasoning_summary": "Summary.",
    "laws_referenced": ["criminal code"],
    "confidence": 80,
}


# --- Research agent (RAG) ----------------------------------------------------

def test_build_research_queries_from_analysis():
    qs = build_research_queries(CASE, {"legal_issues": ["fraud intent"], "possible_charges": ["Fraud"]})
    assert "fraud intent" in qs and "Fraud" in qs


def test_research_agent_uses_injected_retriever_and_grounds_citations():
    retriever = FakeRetriever()
    llm = ScriptedLLM()
    out = research_case(CASE, {"legal_issues": ["fraud"], "possible_charges": ["Fraud"]}, retriever=retriever, llm=llm)
    assert retriever.queries, "retriever should have been called"
    # Citations come from the (fake) knowledge base, not invented by the LLM.
    acts = {cit["act"] for cit in out["citations"]}
    assert "Nepal Pharmacy Council Act, 2057" in acts
    assert out["confidence"] == 80


def test_research_agent_honest_when_no_hits():
    out = research_case(CASE, {"legal_issues": ["x"]}, retriever=FakeRetriever(chunks=[]), llm=ScriptedLLM())
    assert out["citations"] == []
    assert "No directly applicable" in out["research_summary"]


# --- Full pipeline -----------------------------------------------------------

def test_full_pipeline_structure_and_agent_count():
    llm = ScriptedLLM()
    retriever = FakeRetriever()
    report = run_case_intelligence(CASE, llm=llm, retriever=retriever)

    assert set(report.keys()) >= {
        "case", "analysis", "timeline", "evidence", "research",
        "prosecution", "defense", "judge", "verdict", "disclaimer",
    }
    # 8 agents => 8 LLM calls (research also calls once, on top of retrieval).
    assert llm.calls == 8
    # RAG grounding surfaced real citations into the report.
    assert report["research"]["citations"]
    # Internal retrieval context is NOT leaked to the client report.
    assert "context" not in report["research"]

    assert report["verdict"]["likely_outcome"] == "Likely Conviction"
    assert report["evidence"]["overall_strength"] == "Strong"
    assert report["timeline"]["events"][0]["date"] == "Jan 12"

    # Explainability triple present on every agent.
    for key in ("analysis", "evidence", "research", "prosecution", "defense", "judge", "verdict", "timeline"):
        agent = report[key]
        assert "reasoning_summary" in agent
        assert "laws_referenced" in agent
        assert 0 <= agent["confidence"] <= 100


def test_pipeline_survives_malformed_agent_output():
    report = run_case_intelligence(CASE, llm=ScriptedLLM(reply="not json"), retriever=FakeRetriever())
    # Degrades gracefully; still returns a well-formed report.
    assert report["analysis"]["parse_error"] is True
    assert report["verdict"]["likely_outcome"] == "Uncertain Outcome"  # safe default


def test_verdict_enum_clamped():
    llm = ScriptedLLM(reply=json.dumps({**_SUPERSET, "likely_outcome": "GUILTY", "confidence": 999}))
    report = run_case_intelligence(CASE, llm=llm, retriever=FakeRetriever())
    assert report["verdict"]["likely_outcome"] == "Uncertain Outcome"
    assert report["verdict"]["confidence"] == 100


# --- Demo dataset ------------------------------------------------------------

def test_demo_cases_present_and_valid():
    assert len(DEMO_CASES) == 6
    types = {d["case_type"] for d in DEMO_CASES}
    assert {"Fraud", "Cybercrime", "Employment Dispute", "Property Dispute", "Murder"} <= types
    for d in DEMO_CASES:
        assert d["title"] and len(d["description"]) > 50 and d["id"]


# --- Document extraction -----------------------------------------------------

def test_extract_txt():
    text, kind = extract_document_text("case.txt", "text/plain", b"Party A sued Party B.")
    assert kind == "txt" and "Party A" in text


def test_extract_unsupported():
    with pytest.raises(UnsupportedDocument):
        extract_document_text("evil.exe", "application/octet-stream", b"MZ...")


def test_extract_json_tolerates_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
