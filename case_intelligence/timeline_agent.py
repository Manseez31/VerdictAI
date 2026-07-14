"""Feature 8 — Timeline Generator.

Builds a chronological case timeline from the scenario and the analyzer's
extracted dates. Only uses events actually stated or clearly implied — it does
not invent dates.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import common as c


def build_timeline(case: Dict[str, str], analysis: Dict[str, Any], llm) -> Dict[str, Any]:
    seed_dates = analysis.get("dates") or []
    prompt = f"""{c.SAFETY_PREAMBLE}
ROLE: You are the TIMELINE agent. Build a chronological timeline of the case
using ONLY events stated or clearly implied in the scenario. Do not invent
dates. If a date is relative or approximate (e.g. "later that week"), keep it as
stated. Order events chronologically.

{c.case_block(case)}

DATES ALREADY EXTRACTED (may be incomplete):
{c.upstream("DATES", seed_dates, 1200)}

Return ONLY this JSON schema:
{{
  "events": [
    {{"date": "as stated (e.g. 'Jan 12', '2024-02-03', or 'Later')", "event": "what happened", "significance": "why it matters legally (optional)"}}
  ],
  "reasoning_summary": "1-3 sentence note on the timeline",
  "confidence": 0-100
}}"""
    raw = c.run_agent(llm, "Timeline", prompt)

    events: List[Dict[str, str]] = []
    if isinstance(raw.get("events"), list):
        for item in raw["events"][:40]:
            if not isinstance(item, dict):
                continue
            event = c.text(item.get("event"), 500)
            if not event:
                continue
            events.append({
                "date": c.text(item.get("date"), 60) or "—",
                "event": event,
                "significance": c.text(item.get("significance"), 400),
            })

    result = {
        "events": events,
        "reasoning_summary": c.text(raw.get("reasoning_summary")),
        "laws_referenced": [],
        "confidence": c.confidence(raw.get("confidence")),
    }
    return c.with_parse_flag(result, raw)
