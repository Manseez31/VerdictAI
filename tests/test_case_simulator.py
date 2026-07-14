"""Offline tests for the multi-agent case simulator (LLM faked)."""

import json
from types import SimpleNamespace

import pytest

from case_simulator import (
    DISCLAIMER,
    analyze_case,
    extract_json,
    judge_case,
    run_case_simulation,
)


CASE = {
    "title": "Fake Investment Scheme",
    "description": "A promised investors 20% monthly returns, collected 5M NPR via bank transfers, and paid old investors with new deposits until it collapsed.",
    "jurisdiction": "Nepal",
    "case_type": "Fraud",
}


class ScriptedLLM:
    """Returns queued replies in order (one per agent call)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.replies.pop(0))


def _agent_json(**overrides):
    base = {
        "facts": ["A collected 5M NPR"],
        "legal_issues": ["Was there intent to defraud?"],
        "possible_charges": ["Fraud"],
        "parties": [{"name": "A", "role": "accused"}],
        "evidence": ["bank transfer records"],
        "arguments": ["The scheme paid old investors with new deposits"],
        "supporting_evidence": ["bank records show circular payments"],
        "legal_references": ["fraud provisions of the criminal code"],
        "why_charges_apply": "Deception for financial gain.",
        "prosecution_weaknesses": ["intent not directly evidenced"],
        "procedural_issues": ["how records were obtained"],
        "alternative_interpretations": ["a failed but genuine business"],
        "evidence_assessments": [
            {"evidence": "bank records", "strength": "Strong", "reasoning": "official and verifiable"}
        ],
        "witness_reliability": "Investor testimony is consistent.",
        "document_quality": "Bank records are official.",
        "digital_evidence_quality": "No digital evidence described",
        "chain_of_custody_concerns": [],
        "overall_strength": "Strong",
        "legal_reasoning": "The evidence supports the elements of fraud.",
        "findings": ["Deception occurred"],
        "verdict": "Likely Conviction",
        "verdict_reasoning": "Strong documentary evidence.",
        "reasoning_summary": "Summary.",
        "laws_referenced": ["criminal code"],
        "confidence": 80,
    }
    base.update(overrides)
    return json.dumps(base)


# --- extract_json robustness -------------------------------------------------

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_fences_and_chatter():
    raw = 'Here you go:\n```json\n{"a": [1, 2]}\n```\nHope that helps!'
    assert extract_json(raw) == {"a": [1, 2]}


def test_extract_json_rejects_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here at all")


# --- single agents -----------------------------------------------------------

def test_analyzer_normalizes_output():
    llm = ScriptedLLM([_agent_json(confidence="85")])
    out = analyze_case(CASE, llm)
    assert out["facts"] == ["A collected 5M NPR"]
    assert out["parties"] == [{"name": "A", "role": "accused"}]
    assert out["confidence"] == 85
    # Safety preamble + case fields reached the prompt
    assert "Do NOT provide instructions" in llm.prompts[0]
    assert "Fake Investment Scheme" in llm.prompts[0]


def test_judge_clamps_bad_verdict_and_confidence():
    llm = ScriptedLLM([_agent_json(verdict="GUILTY!!!", confidence=250)])
    out = judge_case(CASE, {}, {}, {}, {}, llm)
    assert out["verdict"] == "Uncertain Outcome"   # invalid enum -> safe default
    assert out["confidence"] == 100                # clamped


def test_agent_survives_malformed_json():
    llm = ScriptedLLM(["I refuse to answer in JSON, but here is my analysis..."])
    out = analyze_case(CASE, llm)
    assert out["parse_error"] is True
    assert "refuse" in out["reasoning_summary"]
    assert out["facts"] == []                      # degraded but structurally valid


# --- full pipeline -----------------------------------------------------------

def test_full_pipeline_structure_and_order():
    llm = ScriptedLLM([_agent_json() for _ in range(5)])
    report = run_case_simulation(CASE, llm=llm)

    assert set(report.keys()) >= {
        "case", "analysis", "prosecution", "defense",
        "evidence_review", "judge", "disclaimer", "generated_at",
    }
    assert report["disclaimer"] == DISCLAIMER
    assert report["judge"]["verdict"] == "Likely Conviction"
    assert report["evidence_review"]["overall_strength"] == "Strong"
    assert len(llm.prompts) == 5

    # Sequential context flow: each downstream agent sees upstream output.
    assert "CASE ANALYSIS" in llm.prompts[1]        # prosecution sees analysis
    assert "PROSECUTION VIEW" in llm.prompts[2]     # defense sees prosecution
    assert "DEFENSE VIEW" in llm.prompts[3]         # evidence review sees defense
    assert "EVIDENCE REVIEW" in llm.prompts[4]      # judge sees evidence review

    # Explainability: every agent carries the triple.
    for key in ("analysis", "prosecution", "defense", "evidence_review", "judge"):
        agent = report[key]
        assert "reasoning_summary" in agent
        assert "laws_referenced" in agent
        assert 0 <= agent["confidence"] <= 100
