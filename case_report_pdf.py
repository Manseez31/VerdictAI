"""PDF export for Legal Case Simulator reports.

Renders an already-generated simulation report (the dict produced by
case_simulator.run_case_simulation) into a paginated A4 PDF using PyMuPDF's
Story engine — pymupdf is already a project dependency, so no new packages.

This is pure presentation: it never re-runs any agent.
"""

from __future__ import annotations

import html
import io
import logging
from typing import Any, Dict, List

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

_CSS = """
body { font-family: sans-serif; font-size: 10pt; color: #1e293b; line-height: 1.45; }
h1 { font-size: 19pt; color: #1e1b4b; margin: 0 0 2pt 0; }
h2 { font-size: 13pt; color: #312e81; margin: 14pt 0 4pt 0; border-bottom: 0.8pt solid #c7d2fe; padding-bottom: 2pt; }
h3 { font-size: 10.5pt; color: #334155; margin: 8pt 0 2pt 0; }
p { margin: 3pt 0; }
ul { margin: 2pt 0 6pt 14pt; }
li { margin: 1.5pt 0; }
.meta { color: #64748b; font-size: 9pt; margin-bottom: 6pt; }
.badge { color: #312e81; font-weight: bold; }
.disclaimer { font-size: 8.5pt; color: #64748b; border: 0.8pt solid #cbd5e1; padding: 6pt; margin-top: 10pt; }
.conf { color: #475569; font-size: 9pt; font-style: italic; }
.verdict { font-size: 12pt; font-weight: bold; color: #1e1b4b; }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _ul(items: List[Any]) -> str:
    items = [i for i in (items or []) if str(i).strip()]
    if not items:
        return "<p><i>None identified.</i></p>"
    return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>"


def _para(text: Any, fallback: str = "—") -> str:
    text = str(text or "").strip()
    return f"<p>{_esc(text) if text else fallback}</p>"


def _conf(agent: Dict[str, Any]) -> str:
    return f'<p class="conf">Confidence: {int(agent.get("confidence", 0))}/100</p>'


def _laws(agent: Dict[str, Any]) -> str:
    return "<h3>Laws referenced</h3>" + _ul(agent.get("laws_referenced"))


def _report_html(report: Dict[str, Any]) -> str:
    case = report.get("case", {})
    analysis = report.get("analysis", {})
    prosecution = report.get("prosecution", {})
    defense = report.get("defense", {})
    evidence = report.get("evidence_review", {})
    judge = report.get("judge", {})

    parties = analysis.get("parties") or []
    parties_txt = [
        f"{p.get('name', '')} ({p.get('role') or 'party'})" if isinstance(p, dict) else str(p)
        for p in parties
    ]

    assessments = evidence.get("evidence_assessments") or []
    assessment_items = [
        f"{a.get('evidence', '')} — {a.get('strength', 'Moderate')}: {a.get('reasoning', '')}"
        for a in assessments if isinstance(a, dict)
    ]

    return f"""
<h1>Legal Case Simulation Report</h1>
<p class="meta">
  {_esc(case.get('title'))} &nbsp;|&nbsp; Jurisdiction: {_esc(case.get('jurisdiction'))}
  &nbsp;|&nbsp; Type: {_esc(case.get('case_type'))} &nbsp;|&nbsp; Generated: {_esc(report.get('generated_at'))}
</p>
<p><b>Scenario:</b> {_esc(case.get('description'))}</p>

<h2>1. Case Summary</h2>
{_para(analysis.get('reasoning_summary'))}
<h3>Facts</h3>{_ul(analysis.get('facts'))}
<h3>Legal issues</h3>{_ul(analysis.get('legal_issues'))}
<h3>Possible charges</h3>{_ul(analysis.get('possible_charges'))}
<h3>Parties</h3>{_ul(parties_txt)}
<h3>Evidence identified</h3>{_ul(analysis.get('evidence'))}
{_laws(analysis)}{_conf(analysis)}

<h2>2. Prosecution View</h2>
{_para(prosecution.get('reasoning_summary'))}
<h3>Arguments</h3>{_ul(prosecution.get('arguments'))}
<h3>Supporting evidence</h3>{_ul(prosecution.get('supporting_evidence'))}
<h3>Why charges may apply</h3>{_para(prosecution.get('why_charges_apply'))}
{_laws(prosecution)}{_conf(prosecution)}

<h2>3. Defense View</h2>
{_para(defense.get('reasoning_summary'))}
<h3>Arguments</h3>{_ul(defense.get('arguments'))}
<h3>Weaknesses in prosecution</h3>{_ul(defense.get('prosecution_weaknesses'))}
<h3>Procedural issues</h3>{_ul(defense.get('procedural_issues'))}
<h3>Alternative interpretations</h3>{_ul(defense.get('alternative_interpretations'))}
{_laws(defense)}{_conf(defense)}

<h2>4. Evidence Analysis</h2>
<p><span class="badge">Overall evidence strength: {_esc(evidence.get('overall_strength', 'Moderate'))}</span></p>
{_para(evidence.get('reasoning_summary'))}
<h3>Item-by-item assessment</h3>{_ul(assessment_items)}
<h3>Witness reliability</h3>{_para(evidence.get('witness_reliability'))}
<h3>Document quality</h3>{_para(evidence.get('document_quality'))}
<h3>Digital evidence quality</h3>{_para(evidence.get('digital_evidence_quality'))}
<h3>Chain-of-custody concerns</h3>{_ul(evidence.get('chain_of_custody_concerns'))}
{_laws(evidence)}{_conf(evidence)}

<h2>5. Judge Analysis</h2>
<p class="verdict">Simulated verdict tendency: {_esc(judge.get('verdict', 'Uncertain Outcome'))}</p>
{_para(judge.get('verdict_reasoning'))}
<h3>Legal reasoning</h3>{_para(judge.get('legal_reasoning'))}
<h3>Findings</h3>{_ul(judge.get('findings'))}
{_laws(judge)}{_conf(judge)}

<div class="disclaimer">{_esc(report.get('disclaimer', ''))}</div>
"""


def build_case_report_pdf(report: Dict[str, Any]) -> bytes:
    """Render the report dict to PDF bytes (A4, paginated)."""
    story = fitz.Story(html=_report_html(report), user_css=_CSS)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (40, 42, -40, -48)

    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()

    pdf = buf.getvalue()
    logger.info("Case report PDF generated: %d bytes", len(pdf))
    return pdf
