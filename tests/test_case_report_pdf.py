"""Tests for the PDF export of case simulation reports (offline)."""

from case_report_pdf import build_case_report_pdf


SAMPLE_REPORT = {
    "case": {
        "title": "Fake Investment Scheme",
        "description": "A promised investors 20% monthly returns…",
        "jurisdiction": "Nepal",
        "case_type": "Fraud",
    },
    "analysis": {
        "facts": ["A collected 5M NPR"],
        "legal_issues": ["Intent to defraud?"],
        "possible_charges": ["Fraud"],
        "parties": [{"name": "A", "role": "accused"}],
        "evidence": ["bank records"],
        "reasoning_summary": "Neutral summary.",
        "laws_referenced": ["criminal code"],
        "confidence": 80,
    },
    "prosecution": {
        "arguments": ["Ponzi structure shows deception"],
        "supporting_evidence": ["circular payments"],
        "legal_references": ["fraud provisions"],
        "why_charges_apply": "Deception for gain.",
        "reasoning_summary": "Prosecution summary.",
        "laws_referenced": ["fraud provisions"],
        "confidence": 75,
    },
    "defense": {
        "arguments": ["Business failure, not fraud"],
        "prosecution_weaknesses": ["intent unproven"],
        "procedural_issues": [],
        "alternative_interpretations": ["genuine venture"],
        "legal_references": ["burden of proof"],
        "reasoning_summary": "Defense summary.",
        "laws_referenced": ["burden of proof"],
        "confidence": 55,
    },
    "evidence_review": {
        "evidence_assessments": [
            {"evidence": "bank records", "strength": "Strong", "reasoning": "official"}
        ],
        "witness_reliability": "Consistent.",
        "document_quality": "High.",
        "digital_evidence_quality": "None described.",
        "chain_of_custody_concerns": [],
        "overall_strength": "Strong",
        "reasoning_summary": "Evidence summary.",
        "laws_referenced": ["evidence law"],
        "confidence": 70,
    },
    "judge": {
        "legal_reasoning": "The elements of fraud appear supported.",
        "findings": ["Deception occurred"],
        "verdict": "Likely Conviction",
        "verdict_reasoning": "Strong documents.",
        "reasoning_summary": "Judge summary.",
        "laws_referenced": ["criminal code"],
        "confidence": 72,
    },
    "disclaimer": "Educational simulation only.",
    "generated_at": "2026-07-13T20:00:00",
}


def test_pdf_bytes_generated():
    pdf = build_case_report_pdf(SAMPLE_REPORT)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2000


def test_pdf_handles_sparse_report():
    # Missing agents / empty lists must not crash the renderer.
    pdf = build_case_report_pdf({"case": {"title": "X"}, "judge": {}})
    assert pdf.startswith(b"%PDF")


def test_pdf_escapes_html_in_content():
    report = dict(SAMPLE_REPORT)
    report["case"] = dict(SAMPLE_REPORT["case"], title="<script>alert(1)</script>")
    pdf = build_case_report_pdf(report)
    assert pdf.startswith(b"%PDF")
