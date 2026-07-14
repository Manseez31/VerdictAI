// intelligence.js — Legal Case Intelligence dashboard.
// Renders the 8-section multi-agent report (Summary, Timeline, Evidence,
// Applicable Laws, Prosecution, Defense, Judge, Verdict) with explainability,
// document upload, demo loading, and a bilingual (EN ⇄ ने) toggle.

import { el, qs, clear } from "./dom.js";
import { icon } from "./icons.js";
import { initTheme, bindThemeToggles } from "./theme.js";
import { renderRichText } from "./format.js";
import {
  runCaseIntelligence, fetchDemoCases, extractDocument, translateText, ApiError,
} from "./api.js";

const LAST_KEY = "ai-advocate/case-intel/last";

const els = {
  form: qs("#caseForm"),
  title: qs("#caseTitle"),
  type: qs("#caseType"),
  jurisdiction: qs("#caseJurisdiction"),
  description: qs("#caseDescription"),
  charCount: qs("#charCount"),
  runBtn: qs("#runBtn"),
  results: qs("#results"),
  demoPicker: qs("#demoPicker"),
  fileInput: qs("#fileInput"),
  uploadLabel: qs("#uploadLabel"),
  langToggle: qs("#langToggle"),
};

let running = false;
let report = null;
let lang = "en"; // 'en' | 'ne'

// The 8 agent stages that show during loading.
const STAGES = [
  { key: "analysis", label: "Case Analyzer", icon: "search" },
  { key: "evidence", label: "Evidence Analyzer", icon: "layers" },
  { key: "research", label: "Legal Research (RAG)", icon: "scroll" },
  { key: "timeline", label: "Timeline", icon: "calendar" },
  { key: "prosecution", label: "Prosecutor", icon: "gavel" },
  { key: "defense", label: "Defense", icon: "shield" },
  { key: "judge", label: "Judge", icon: "scale" },
  { key: "verdict", label: "Verdict", icon: "flag" },
];

// The 8 dashboard sections (Feature 9).
const SECTIONS = [
  { id: "summary", label: "Summary", icon: "file" },
  { id: "timeline", label: "Timeline", icon: "calendar" },
  { id: "evidence", label: "Evidence", icon: "layers" },
  { id: "laws", label: "Applicable Laws", icon: "scroll" },
  { id: "prosecution", label: "Prosecution", icon: "gavel" },
  { id: "defense", label: "Defense", icon: "shield" },
  { id: "judge", label: "Judge", icon: "scale" },
  { id: "verdict", label: "Verdict", icon: "flag" },
];
let activeSection = "summary";

// ============================================================================
//  Presentational helpers
// ============================================================================

function ConfidenceBar(value) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const tone = v >= 70 ? "bg-emerald-500" : v >= 40 ? "bg-amber-500" : "bg-rose-500";
  return el("div", { class: "flex items-center gap-3", role: "meter", "aria-valuenow": String(v), "aria-valuemin": "0", "aria-valuemax": "100" }, [
    el("div", { class: "h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700" }, [
      el("div", { class: `h-full rounded-full ${tone}`, style: `width:${v}%` }),
    ]),
    el("span", { class: "text-xs font-semibold tabular-nums text-slate-500 dark:text-slate-400" }, `${v}/100`),
  ]);
}

function StrengthBadge(strength) {
  const tones = {
    Strong: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/25",
    Moderate: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/25",
    Weak: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-400/25",
    Missing: "bg-slate-100 text-slate-600 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-400/20",
  };
  return el("span", { class: `inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${tones[strength] || tones.Moderate}` }, strength || "Moderate");
}

/** Explainability footer: reasoning + laws + confidence (Feature: Explainability). */
function ExplainBlock(agent) {
  return el("div", { class: "mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-800/40" }, [
    el("h4", { class: "mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Reasoning summary"),
    el("p", { class: "answer-body text-sm leading-relaxed text-slate-600 dark:text-slate-300", html: renderRichText(agent.reasoning_summary || "—") }),
    el("h4", { class: "mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Laws referenced"),
    (agent.laws_referenced || []).length
      ? el("ul", { class: "list-disc space-y-0.5 pl-5 text-sm text-slate-600 dark:text-slate-300" }, agent.laws_referenced.map((l) => el("li", { html: renderRichText(l) })))
      : el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "None referenced."),
    el("h4", { class: "mb-1.5 mt-3 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Confidence"),
    ConfidenceBar(agent.confidence),
  ]);
}

function ListBlock(title, items, { rich = false } = {}) {
  return el("div", { class: "mb-4" }, [
    el("h4", { class: "mb-1.5 text-sm font-bold text-slate-800 dark:text-slate-100" }, title),
    (items || []).length
      ? el("ul", { class: "list-disc space-y-1 pl-5 text-sm leading-relaxed text-slate-700 dark:text-slate-200" },
          items.map((i) => el("li", rich ? { html: renderRichText(i) } : {}, rich ? undefined : i)))
      : el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "None identified."),
  ]);
}

function TextBlock(title, text) {
  return el("div", { class: "mb-4" }, [
    el("h4", { class: "mb-1.5 text-sm font-bold text-slate-800 dark:text-slate-100" }, title),
    el("p", { class: "answer-body text-sm leading-relaxed text-slate-700 dark:text-slate-200", html: renderRichText(text || "—") }),
  ]);
}

function Card(children) {
  return el("div", { class: "rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900" }, children);
}

// ============================================================================
//  Section renderers (English structured view)
// ============================================================================

function summarySection(r) {
  const a = r.analysis || {};
  const parties = (a.parties || []).map((p) => (typeof p === "object" ? `${p.name}${p.role ? ` — ${p.role}` : ""}` : String(p)));
  return el("div", {}, [
    TextBlock("Case summary", a.summary || a.reasoning_summary),
    el("div", { class: "grid gap-3 sm:grid-cols-2" }, [
      Card([ListBlock("Parties", parties)]),
      Card([ListBlock("Locations", a.locations)]),
    ]),
    el("div", { class: "mt-3" }, [
      ListBlock("Alleged actions (unproven)", a.alleged_actions),
      ListBlock("Legal issues", a.legal_issues),
      ListBlock("Possible charges", a.possible_charges),
    ]),
    ExplainBlock(a),
  ]);
}

function timelineSection(r) {
  const events = (r.timeline || {}).events || [];
  if (!events.length) return el("div", {}, [el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "No datable events were identified in this scenario."), ExplainBlock(r.timeline || {})]);
  return el("div", {}, [
    el("ol", { class: "relative ml-3 border-l-2 border-slate-200 dark:border-slate-700" },
      events.map((ev) =>
        el("li", { class: "mb-5 ml-5 anim-rise" }, [
          el("span", { class: "absolute -left-[9px] flex h-4 w-4 items-center justify-center rounded-full bg-brand-500 ring-4 ring-slate-50 dark:ring-slate-950" }),
          el("time", { class: "text-xs font-bold uppercase tracking-wide text-brand-600 dark:text-brand-300" }, ev.date || "—"),
          el("p", { class: "mt-0.5 text-sm font-medium text-slate-800 dark:text-slate-100" }, ev.event || ""),
          ev.significance ? el("p", { class: "mt-0.5 text-xs text-slate-500 dark:text-slate-400" }, ev.significance) : null,
        ]))),
    ExplainBlock(r.timeline || {}),
  ]);
}

function evidenceSection(r) {
  const e = r.evidence || {};
  const items = (e.items || []).map((it) =>
    Card([
      el("div", { class: "mb-1 flex items-center justify-between gap-2" }, [
        el("span", { class: "min-w-0 flex-1 text-sm font-semibold text-slate-800 dark:text-slate-100", title: it.evidence }, it.evidence),
        StrengthBadge(it.classification),
      ]),
      el("p", { class: "text-sm leading-relaxed text-slate-600 dark:text-slate-300" }, it.reasoning || ""),
      el("div", { class: "mt-2" }, [ConfidenceBar(it.confidence)]),
    ]));
  return el("div", {}, [
    el("div", { class: "mb-4 flex items-center gap-2" }, [
      el("span", { class: "text-sm font-bold text-slate-800 dark:text-slate-100" }, "Overall evidence strength:"),
      StrengthBadge(e.overall_strength),
    ]),
    items.length ? el("div", { class: "mb-4 space-y-2.5" }, items) : el("p", { class: "mb-4 text-sm text-slate-400 dark:text-slate-500" }, "No individual evidence assessed."),
    el("div", { class: "grid gap-3 sm:grid-cols-3" }, [
      Card([el("h4", { class: "mb-1 text-xs font-bold text-slate-700 dark:text-slate-200" }, "Witness reliability"), el("p", { class: "text-xs text-slate-600 dark:text-slate-300" }, e.witness_reliability || "—")]),
      Card([el("h4", { class: "mb-1 text-xs font-bold text-slate-700 dark:text-slate-200" }, "Document quality"), el("p", { class: "text-xs text-slate-600 dark:text-slate-300" }, e.document_quality || "—")]),
      Card([el("h4", { class: "mb-1 text-xs font-bold text-slate-700 dark:text-slate-200" }, "Digital evidence"), el("p", { class: "text-xs text-slate-600 dark:text-slate-300" }, e.digital_evidence_quality || "—")]),
    ]),
    el("div", { class: "mt-3" }, [
      ListBlock("Missing evidence", e.missing_evidence),
      ListBlock("Chain-of-custody concerns", e.chain_of_custody_concerns),
    ]),
    ExplainBlock(e),
  ]);
}

function lawsSection(r) {
  const research = r.research || {};
  const laws = research.applicable_laws || [];
  const citations = research.citations || [];
  return el("div", {}, [
    TextBlock("Research summary", research.research_summary),
    el("h4", { class: "mb-2 text-sm font-bold text-slate-800 dark:text-slate-100" }, "Applicable provisions"),
    laws.length
      ? el("div", { class: "mb-4 space-y-2.5" }, laws.map((l) =>
          Card([
            el("div", { class: "mb-1 flex flex-wrap items-center gap-2" }, [
              el("span", { class: "text-sm font-semibold text-slate-800 dark:text-slate-100" }, l.act || "Act"),
              l.section ? el("span", { class: "rounded-md bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300" }, `धारा ${l.section}`) : null,
            ]),
            l.provision ? el("p", { class: "text-sm text-slate-600 dark:text-slate-300" }, l.provision) : null,
            l.relevance ? el("p", { class: "mt-1 text-xs italic text-slate-500 dark:text-slate-400" }, `Relevance: ${l.relevance}`) : null,
          ]))
      : el("p", { class: "mb-4 text-sm text-slate-400 dark:text-slate-500" }, "No specific provisions were retrieved from the knowledge base."),
    el("h4", { class: "mb-2 text-sm font-bold text-slate-800 dark:text-slate-100" }, `Citations from the knowledge base (${citations.length})`),
    citations.length
      ? el("div", { class: "flex flex-wrap gap-2" }, citations.map((cit) =>
          el("span", { class: "inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" }, [
            el("span", { class: "text-brand-500", html: icon("scale", "w-3.5 h-3.5") }),
            cit.act + (cit.section ? `, धारा ${cit.section}` : ""),
          ])))
      : el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "No citations."),
    ExplainBlock(research),
  ]);
}

function prosecutionSection(r) {
  const p = r.prosecution || {};
  return el("div", {}, [
    ListBlock("Arguments", p.arguments, { rich: true }),
    ListBlock("Evidence-based arguments", p.evidence_based_arguments, { rich: true }),
    ListBlock("Aggravating factors", p.aggravating_factors),
    TextBlock("Why the charges may apply", p.why_charges_apply),
    ListBlock("Legal references", p.legal_references, { rich: true }),
    ExplainBlock(p),
  ]);
}

function defenseSection(r) {
  const d = r.defense || {};
  return el("div", {}, [
    ListBlock("Defense arguments", d.arguments, { rich: true }),
    ListBlock("Weaknesses in the prosecution", d.prosecution_weaknesses),
    ListBlock("Evidentiary concerns", d.evidentiary_concerns),
    ListBlock("Alternative interpretations", d.alternative_interpretations),
    ListBlock("Mitigating circumstances", d.mitigating_circumstances),
    ListBlock("Legal references", d.legal_references, { rich: true }),
    ExplainBlock(d),
  ]);
}

function judgeSection(r) {
  const j = r.judge || {};
  return el("div", {}, [
    TextBlock("Legal reasoning", j.legal_reasoning),
    el("div", { class: "grid gap-3 sm:grid-cols-2" }, [
      Card([el("h4", { class: "mb-1 text-xs font-bold text-slate-700 dark:text-slate-200" }, "Prosecution assessment"), el("p", { class: "text-sm text-slate-600 dark:text-slate-300" }, j.prosecution_assessment || "—")]),
      Card([el("h4", { class: "mb-1 text-xs font-bold text-slate-700 dark:text-slate-200" }, "Defense assessment"), el("p", { class: "text-sm text-slate-600 dark:text-slate-300" }, j.defense_assessment || "—")]),
    ]),
    el("div", { class: "mt-3 flex flex-wrap items-center gap-4" }, [
      el("div", { class: "flex items-center gap-2" }, [el("span", { class: "text-sm font-bold text-slate-700 dark:text-slate-200" }, "Evidence quality:"), StrengthBadge(j.evidence_quality)]),
    ]),
    TextBlock("Citation quality", j.citation_quality),
    ListBlock("Findings", j.findings),
    ExplainBlock(j),
  ]);
}

function verdictSection(r) {
  const v = r.verdict || {};
  return el("div", {}, [
    TextBlock("Rationale", v.rationale),
    TextBlock("Uncertainty analysis", v.uncertainty_analysis),
    ListBlock("Key factors", v.key_factors),
    el("p", { class: "mt-3 rounded-lg bg-amber-50 p-3 text-xs font-medium text-amber-800 dark:bg-amber-950/40 dark:text-amber-300" },
      "⚠ Educational prediction only — this is not legal advice and not a real verdict."),
    ExplainBlock(v),
  ]);
}

const SECTION_BUILDERS = {
  summary: summarySection, timeline: timelineSection, evidence: evidenceSection,
  laws: lawsSection, prosecution: prosecutionSection, defense: defenseSection,
  judge: judgeSection, verdict: verdictSection,
};

// ============================================================================
//  Bilingual (Feature 10): translate the active section's narrative to Nepali.
//  Reuses /translate (target_lang='ne'); no retrieval re-run; citations kept.
// ============================================================================

function collectSectionText(r, sectionId) {
  // Build a readable English text block for the section (for translation).
  const lines = [];
  const push = (label, val) => {
    if (Array.isArray(val)) val.forEach((v) => v && lines.push(`- ${typeof v === "object" ? JSON.stringify(v) : v}`));
    else if (val) lines.push(String(val));
  };
  const s = {
    summary: () => { const a = r.analysis || {}; push("", a.summary); push("", a.legal_issues); push("", a.possible_charges); },
    timeline: () => ((r.timeline || {}).events || []).forEach((e) => lines.push(`${e.date}: ${e.event}`)),
    evidence: () => { const e = r.evidence || {}; push("", e.reasoning_summary); (e.items || []).forEach((it) => lines.push(`- ${it.evidence} (${it.classification}): ${it.reasoning}`)); push("", e.missing_evidence); },
    laws: () => { const rr = r.research || {}; push("", rr.research_summary); (rr.applicable_laws || []).forEach((l) => lines.push(`- ${l.act}${l.section ? `, धारा ${l.section}` : ""}: ${l.provision || ""}`)); },
    prosecution: () => { const p = r.prosecution || {}; push("", p.arguments); push("", p.why_charges_apply); push("", p.aggravating_factors); },
    defense: () => { const d = r.defense || {}; push("", d.arguments); push("", d.prosecution_weaknesses); push("", d.mitigating_circumstances); },
    judge: () => { const j = r.judge || {}; push("", j.legal_reasoning); push("", j.findings); },
    verdict: () => { const v = r.verdict || {}; push("", v.rationale); push("", v.uncertainty_analysis); },
  }[sectionId];
  if (s) s();
  return lines.join("\n");
}

async function nepaliSectionEl(sectionId) {
  report._ne = report._ne || {};
  if (report._ne[sectionId]) {
    return el("div", { class: "answer-body font-deva text-sm leading-relaxed text-slate-700 dark:text-slate-200", html: renderRichText(report._ne[sectionId]) });
  }
  const source = collectSectionText(report, sectionId);
  if (!source.trim()) return el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "No content to translate.");

  const loading = el("div", { class: "flex items-center gap-2 text-sm text-brand-600 dark:text-brand-300", role: "status" }, [
    el("span", { class: "h-4 w-4 animate-spin rounded-full border-2 border-brand-400/40 border-t-brand-500" }),
    "अनुवाद हुँदैछ… (Translating…)",
  ]);
  // Translate asynchronously; caller mounts `loading`, we swap in place.
  translateText(source, { targetLang: "ne" })
    .then((ne) => {
      report._ne[sectionId] = ne;
      persistLast();
      if (lang === "ne" && activeSection === sectionId) renderPanel();
    })
    .catch(() => {
      loading.replaceWith(el("p", { class: "text-sm text-rose-500 dark:text-rose-400", role: "alert" }, "Translation failed — showing English is still available."));
    });
  return loading;
}

// ============================================================================
//  Verdict banner + tabs + panel
// ============================================================================

function VerdictBanner(r) {
  const v = (r.verdict || {}).likely_outcome || "Uncertain Outcome";
  const tones = {
    "Likely Conviction": "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-200",
    "Uncertain Outcome": "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200",
    "Likely Acquittal": "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-200",
  };
  return el("div", { class: `mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border p-4 ${tones[v]}` }, [
    el("div", { class: "flex items-center gap-3" }, [
      el("span", { class: "flex h-10 w-10 items-center justify-center rounded-xl bg-white/60 dark:bg-black/20", html: icon("flag", "w-5 h-5") }),
      el("div", {}, [
        el("p", { class: "text-[11px] font-semibold uppercase tracking-wider opacity-70" }, "Simulated likely outcome (educational)"),
        el("p", { class: "font-display text-lg font-semibold leading-tight" }, v),
      ]),
    ]),
    el("span", { class: "text-xs font-semibold opacity-80" }, `Confidence: ${(r.verdict || {}).confidence ?? 0}/100`),
  ]);
}

const panel = () => qs("#ci-panel");

function renderPanel() {
  const p = panel();
  if (!p) return;
  clear(p);
  p.setAttribute("aria-labelledby", `citab-${activeSection}`);
  if (lang === "ne") {
    p.append(el("div", { class: "mb-3 rounded-lg bg-slate-100 p-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400" },
      "नेपाली अनुवाद (सारांश) — पूर्ण संरचित विवरण English मा उपलब्ध छ।"));
    nepaliSectionEl(activeSection).then((node) => { if (activeSection && panel()) panel().append(node); });
    return;
  }
  p.append(SECTION_BUILDERS[activeSection](report));
}

function renderTabs(tablist) {
  clear(tablist);
  SECTIONS.forEach((sec) => {
    const selected = sec.id === activeSection;
    tablist.append(el("button", {
      type: "button", role: "tab", id: `citab-${sec.id}`,
      "aria-selected": selected ? "true" : "false", "aria-controls": "ci-panel",
      tabindex: selected ? "0" : "-1",
      class:
        "inline-flex items-center gap-1.5 rounded-t-xl border border-b-0 px-3 py-2.5 text-sm font-semibold transition-colors focus-ring " +
        (selected
          ? "border-slate-200 bg-white text-brand-700 dark:border-slate-800 dark:bg-slate-900 dark:text-brand-300"
          : "border-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/70 dark:hover:text-slate-200"),
      onClick: () => { activeSection = sec.id; renderTabs(tablist); renderPanel(); },
      onKeydown: (e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          const idx = SECTIONS.findIndex((x) => x.id === activeSection);
          const next = (idx + (e.key === "ArrowRight" ? 1 : SECTIONS.length - 1)) % SECTIONS.length;
          activeSection = SECTIONS[next].id;
          renderTabs(tablist); renderPanel();
          tablist.querySelector(`#citab-${activeSection}`)?.focus();
        }
      },
      html: icon(sec.icon, "w-4 h-4") + `<span class="hidden sm:inline">${sec.label}</span>`,
    }));
  });
}

function renderLangToggle() {
  clear(els.langToggle);
  els.langToggle.classList.toggle("hidden", !report);
  if (!report) return;
  const seg = (value, label) => el("button", {
    type: "button", role: "radio", "aria-checked": lang === value ? "true" : "false",
    class: "rounded-md px-2.5 py-1 text-xs font-semibold transition-colors focus-ring " +
      (lang === value ? "bg-brand-600 text-white shadow-sm" : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"),
    onClick: () => { if (lang !== value) { lang = value; renderLangToggle(); renderPanel(); } },
  }, label);
  els.langToggle.append(el("div", {
    class: "flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 dark:border-slate-700 dark:bg-slate-900",
    role: "radiogroup", "aria-label": "Answer language",
  }, [seg("en", "EN"), seg("ne", "ने")]));
}

function renderReport(r) {
  report = r;
  activeSection = "summary";
  lang = "en";
  clear(els.results);
  renderLangToggle();

  const tablist = el("div", { class: "flex flex-wrap gap-1 overflow-x-auto", role: "tablist", "aria-label": "Case sections" });
  const p = el("div", { id: "ci-panel", role: "tabpanel", tabindex: "0", class: "rounded-b-2xl rounded-tr-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900" });

  els.results.append(el("div", { class: "anim-rise" }, [
    VerdictBanner(r),
    tablist,
    p,
    el("p", { class: "mt-4 rounded-lg bg-slate-100 p-3 text-[11px] leading-relaxed text-slate-500 dark:bg-slate-800/60 dark:text-slate-400" }, r.disclaimer || ""),
  ]));
  renderTabs(tablist);
  renderPanel();
}

// ============================================================================
//  States
// ============================================================================

function EmptyState() {
  return el("div", { class: "flex min-h-[440px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white/50 p-8 text-center dark:border-slate-700 dark:bg-slate-900/40 anim-rise" }, [
    el("span", { class: "mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500", html: icon("layers", "w-7 h-7") }),
    el("h2", { class: "font-display text-xl font-semibold text-slate-800 dark:text-slate-100" }, "Analyze a legal case"),
    el("p", { class: "mt-2 max-w-md text-sm leading-relaxed text-slate-500 dark:text-slate-400" },
      "Describe a scenario (or upload a document) and eight AI legal agents will produce a full case dashboard — grounded in the legal knowledge base with real citations."),
    el("div", { class: "mt-5 flex max-w-lg flex-wrap items-center justify-center gap-2" },
      STAGES.map((s) => el("span", { class: "inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400" }, [
        el("span", { class: "text-brand-500", html: icon(s.icon, "w-3.5 h-3.5") }), s.label,
      ]))),
  ]);
}

function LoadingState() {
  const startedAt = Date.now();
  const timer = el("span", { class: "text-xs tabular-nums text-slate-400 dark:text-slate-500" }, "0s");
  const iv = setInterval(() => (timer.textContent = `${Math.round((Date.now() - startedAt) / 1000)}s`), 1000);
  const root = el("div", { class: "rounded-2xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900 anim-rise", role: "status", "aria-label": "Running case intelligence analysis" }, [
    el("div", { class: "mb-1 flex items-center justify-between" }, [
      el("h2", { class: "text-sm font-bold text-slate-800 dark:text-slate-100" }, "Running multi-agent analysis"),
      timer,
    ]),
    el("p", { class: "mb-5 text-xs text-slate-400 dark:text-slate-500" }, "Eight agents run in sequence, including RAG legal research — this typically takes 40–90 seconds."),
    el("div", { class: "grid gap-2.5 sm:grid-cols-2" }, STAGES.map((s, i) =>
      el("div", { class: "flex items-center gap-3 rounded-xl border border-slate-200 px-3.5 py-2.5 dark:border-slate-800 pulse-soft", style: `animation-delay:${i * 160}ms` }, [
        el("span", { class: "flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600 dark:bg-brand-950 dark:text-brand-300", html: icon(s.icon, "w-4 h-4") }),
        el("span", { class: "flex-1 text-sm font-medium text-slate-700 dark:text-slate-200" }, s.label),
        el("span", { class: "h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500 dark:border-slate-600 dark:border-t-brand-400" }),
      ]))),
  ]);
  root._cleanup = () => clearInterval(iv);
  return root;
}

function ErrorState(message, onRetry) {
  return el("div", { class: "rounded-2xl border border-rose-200 bg-rose-50 p-6 dark:border-rose-900/60 dark:bg-rose-950/40 anim-rise", role: "alert" }, [
    el("div", { class: "flex items-start gap-3" }, [
      el("span", { class: "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-rose-100 text-rose-600 dark:bg-rose-900/60 dark:text-rose-300", html: icon("alert", "w-5 h-5") }),
      el("div", { class: "min-w-0 flex-1" }, [
        el("h2", { class: "text-sm font-bold text-rose-800 dark:text-rose-200" }, "Analysis failed"),
        el("p", { class: "mt-1 text-sm text-rose-700/90 dark:text-rose-300/90" }, message),
        onRetry ? el("button", { type: "button", class: "mt-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-rose-700 focus-ring", onClick: onRetry, html: icon("refresh", "w-4 h-4") + "<span>Try again</span>" }) : null,
      ]),
    ]),
  ]);
}

// ============================================================================
//  Flow
// ============================================================================

function setRunBusy(busy) {
  els.runBtn.disabled = busy;
  els.runBtn.innerHTML = busy
    ? '<span class="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"></span><span>Analyzing…</span>'
    : icon("play", "w-4 h-4") + "<span>Run analysis</span>";
}

async function runAnalysis() {
  if (running) return;
  const payload = {
    title: els.title.value.trim(),
    description: els.description.value.trim(),
    jurisdiction: els.jurisdiction.value.trim() || "Nepal",
    case_type: els.type.value,
  };
  if (payload.title.length < 3 || payload.description.length < 30) { els.form.reportValidity(); return; }

  running = true;
  setRunBusy(true);
  clear(els.results);
  const loading = LoadingState();
  els.results.append(loading);
  try {
    const r = await runCaseIntelligence(payload);
    loading._cleanup?.();
    renderReport(r);
    persistLast(payload);
  } catch (err) {
    loading._cleanup?.();
    clear(els.results);
    const message = err instanceof ApiError ? err.message : "An unexpected error occurred. Please try again.";
    els.results.append(ErrorState(message, runAnalysis));
  } finally {
    running = false;
    setRunBusy(false);
  }
}

async function handleUpload(file) {
  if (!file) return;
  els.uploadLabel.textContent = "Reading…";
  try {
    const { text, filename } = await extractDocument(file);
    els.description.value = text;
    updateCharCount();
    if (!els.title.value.trim() && filename) els.title.value = filename.replace(/\.[^.]+$/, "").slice(0, 120);
    els.uploadLabel.textContent = "Replace file";
  } catch (err) {
    els.uploadLabel.textContent = "Upload PDF/DOCX";
    alert(err instanceof ApiError ? err.message : "Could not read that file.");
  } finally {
    els.fileInput.value = "";
  }
}

function updateCharCount() { els.charCount.textContent = `${els.description.value.length} / 12000`; }

function persistLast(input) {
  try {
    const payload = input || JSON.parse(localStorage.getItem(LAST_KEY) || "{}").input;
    localStorage.setItem(LAST_KEY, JSON.stringify({ input: payload, report }));
  } catch { /* storage full — fine */ }
}

function restoreLast() {
  try {
    const raw = localStorage.getItem(LAST_KEY);
    if (!raw) return false;
    const { input, report: saved } = JSON.parse(raw);
    if (input) {
      els.title.value = input.title || "";
      els.description.value = input.description || "";
      els.jurisdiction.value = input.jurisdiction || "Nepal";
      if (input.case_type) els.type.value = input.case_type;
    }
    if (saved) { renderReport(saved); return true; }
  } catch { /* ignore */ }
  return false;
}

async function loadDemos() {
  try {
    const demos = await fetchDemoCases();
    demos.forEach((d) => els.demoPicker.append(el("option", { value: d.id }, `${d.title} (${d.case_type})`)));
    els.demoPicker._cases = Object.fromEntries(demos.map((d) => [d.id, d]));
  } catch { /* demo dropdown just stays minimal */ }
}

function init() {
  initTheme();
  bindThemeToggles();
  setRunBusy(false);
  updateCharCount();
  loadDemos();

  els.form.addEventListener("submit", (e) => { e.preventDefault(); runAnalysis(); });
  els.description.addEventListener("input", updateCharCount);
  els.fileInput.addEventListener("change", (e) => handleUpload(e.target.files?.[0]));
  els.demoPicker.addEventListener("change", (e) => {
    const d = els.demoPicker._cases?.[e.target.value];
    if (!d) return;
    els.title.value = d.title;
    els.description.value = d.description;
    els.jurisdiction.value = d.jurisdiction || "Nepal";
    els.type.value = [...els.type.options].some((o) => o.value === d.case_type) ? d.case_type : "Other";
    updateCharCount();
    e.target.value = "";
  });

  if (!restoreLast()) els.results.append(EmptyState());
}

init();
