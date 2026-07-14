// simulator.js — Legal Case Simulator page: form, multi-agent run, tabbed
// results (Facts / Prosecution / Defense / Evidence Review / Judge Decision),
// explainability (reasoning + laws + confidence per agent), and PDF export.

import { el, qs, clear } from "./dom.js";
import { icon } from "./icons.js";
import { initTheme, bindThemeToggles } from "./theme.js";
import { simulateCase, fetchCaseReportPdf, ApiError } from "./api.js";

const LAST_KEY = "ai-advocate/case-sim/last";

const els = {
  form: qs("#caseForm"),
  title: qs("#caseTitle"),
  type: qs("#caseType"),
  jurisdiction: qs("#caseJurisdiction"),
  description: qs("#caseDescription"),
  charCount: qs("#charCount"),
  runBtn: qs("#runBtn"),
  results: qs("#results"),
  loadExample: qs("#loadExample"),
};

let running = false;
let lastReport = null;

const STAGES = [
  { key: "analysis", label: "Case Analyzer", icon: "search" },
  { key: "prosecution", label: "Prosecution", icon: "gavel" },
  { key: "defense", label: "Defense", icon: "shield" },
  { key: "evidence_review", label: "Evidence Review", icon: "scroll" },
  { key: "judge", label: "Judge", icon: "scale" },
];

const EXAMPLE = {
  title: "Fake Investment Scheme",
  type: "Financial Scam",
  jurisdiction: "Nepal",
  description:
    "Mr. K operated an investment company promising investors a guaranteed 20% monthly return. " +
    "Over 18 months he collected about NPR 50 million from 240 small investors through bank transfers " +
    "and signed agreements. Early investors were paid \"returns\" that actually came from newer investors' " +
    "deposits. No real business activity generating profit has been found. When new deposits slowed, payouts " +
    "stopped and Mr. K claimed business losses. Investigators have bank statements, the signed agreements, " +
    "marketing brochures promising guaranteed returns, and testimony from 12 investors. Mr. K says he ran a " +
    "genuine venture that failed and never intended to cheat anyone.",
};

// ============================================================================
//  Small presentational components
// ============================================================================

/** 0-100 confidence meter with tone by value. */
function ConfidenceBar(value) {
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const tone = v >= 70 ? "bg-emerald-500" : v >= 40 ? "bg-amber-500" : "bg-rose-500";
  return el("div", { class: "flex items-center gap-3", role: "meter", "aria-valuenow": String(v), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": `Confidence ${v} out of 100` }, [
    el("div", { class: "h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700" }, [
      el("div", { class: `h-full rounded-full ${tone}`, style: `width:${v}%` }),
    ]),
    el("span", { class: "text-xs font-semibold tabular-nums text-slate-500 dark:text-slate-400" }, `${v}/100`),
  ]);
}

/** Explainability footer shown for every agent tab. */
function ExplainBlock(agent) {
  return el("div", { class: "mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-800/40" }, [
    el("h4", { class: "mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Reasoning summary"),
    el("p", { class: "text-sm leading-relaxed text-slate-600 dark:text-slate-300" }, agent.reasoning_summary || "—"),
    el("h4", { class: "mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Laws referenced"),
    (agent.laws_referenced || []).length
      ? el("ul", { class: "list-disc space-y-0.5 pl-5 text-sm text-slate-600 dark:text-slate-300" },
          agent.laws_referenced.map((law) => el("li", {}, law)))
      : el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "None referenced."),
    el("h4", { class: "mb-1.5 mt-3 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Confidence"),
    ConfidenceBar(agent.confidence),
  ]);
}

function ListBlock(title, items, { tone = "text-slate-700 dark:text-slate-200" } = {}) {
  return el("div", { class: "mb-4" }, [
    el("h4", { class: "mb-1.5 text-sm font-bold text-slate-800 dark:text-slate-100" }, title),
    (items || []).length
      ? el("ul", { class: `list-disc space-y-1 pl-5 text-sm leading-relaxed ${tone}` }, items.map((i) => el("li", {}, i)))
      : el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "None identified."),
  ]);
}

function TextBlock(title, text) {
  return el("div", { class: "mb-4" }, [
    el("h4", { class: "mb-1.5 text-sm font-bold text-slate-800 dark:text-slate-100" }, title),
    el("p", { class: "whitespace-pre-wrap text-sm leading-relaxed text-slate-700 dark:text-slate-200" }, text || "—"),
  ]);
}

function StrengthBadge(strength) {
  const tones = {
    Strong: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/25",
    Moderate: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/25",
    Weak: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-400/25",
  };
  return el("span", {
    class: `inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${tones[strength] || tones.Moderate}`,
  }, strength || "Moderate");
}

// ============================================================================
//  Result states: empty, loading, error
// ============================================================================

function EmptyState() {
  return el("div", { class: "flex min-h-[420px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white/50 p-8 text-center dark:border-slate-700 dark:bg-slate-900/40 anim-rise" }, [
    el("span", { class: "mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500", html: icon("gavel", "w-7 h-7") }),
    el("h2", { class: "font-display text-xl font-semibold text-slate-800 dark:text-slate-100" }, "Simulate a legal case"),
    el("p", { class: "mt-2 max-w-md text-sm leading-relaxed text-slate-500 dark:text-slate-400" },
      "Describe a hypothetical scenario and five AI legal agents — analyzer, prosecution, defense, evidence reviewer, and judge — will each analyze it from their perspective."),
    el("div", { class: "mt-5 flex flex-wrap items-center justify-center gap-2" },
      STAGES.map((s) =>
        el("span", { class: "inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400" }, [
          el("span", { class: "text-brand-500", html: icon(s.icon, "w-3.5 h-3.5") }), s.label,
        ]))),
  ]);
}

function LoadingState() {
  const startedAt = Date.now();
  const timerEl = el("span", { class: "text-xs tabular-nums text-slate-400 dark:text-slate-500" }, "0s");
  const interval = setInterval(() => {
    timerEl.textContent = `${Math.round((Date.now() - startedAt) / 1000)}s`;
  }, 1000);

  const root = el("div", { class: "rounded-2xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900 anim-rise", role: "status", "aria-label": "Running case simulation" }, [
    el("div", { class: "mb-1 flex items-center justify-between" }, [
      el("h2", { class: "text-sm font-bold text-slate-800 dark:text-slate-100" }, "Running multi-agent analysis"),
      timerEl,
    ]),
    el("p", { class: "mb-5 text-xs text-slate-400 dark:text-slate-500" }, "Five agents run in sequence — this typically takes 20–60 seconds."),
    el("div", { class: "space-y-2.5" },
      STAGES.map((s, i) =>
        el("div", { class: "flex items-center gap-3 rounded-xl border border-slate-200 px-3.5 py-2.5 dark:border-slate-800 pulse-soft", style: `animation-delay:${i * 200}ms` }, [
          el("span", { class: "flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600 dark:bg-brand-950 dark:text-brand-300", html: icon(s.icon, "w-4 h-4") }),
          el("span", { class: "flex-1 text-sm font-medium text-slate-700 dark:text-slate-200" }, s.label),
          el("span", { class: "h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500 dark:border-slate-600 dark:border-t-brand-400" }),
        ]))),
  ]);
  root.addEventListener("DOMNodeRemoved", () => clearInterval(interval));
  root._cleanup = () => clearInterval(interval);
  return root;
}

function ErrorState(message, onRetry) {
  return el("div", { class: "rounded-2xl border border-rose-200 bg-rose-50 p-6 dark:border-rose-900/60 dark:bg-rose-950/40 anim-rise", role: "alert" }, [
    el("div", { class: "flex items-start gap-3" }, [
      el("span", { class: "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-rose-100 text-rose-600 dark:bg-rose-900/60 dark:text-rose-300", html: icon("alert", "w-5 h-5") }),
      el("div", { class: "min-w-0 flex-1" }, [
        el("h2", { class: "text-sm font-bold text-rose-800 dark:text-rose-200" }, "Simulation failed"),
        el("p", { class: "mt-1 text-sm text-rose-700/90 dark:text-rose-300/90" }, message),
        onRetry
          ? el("button", {
              type: "button",
              class: "mt-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-rose-700 focus-ring",
              onClick: onRetry,
              html: icon("refresh", "w-4 h-4") + "<span>Try again</span>",
            })
          : null,
      ]),
    ]),
  ]);
}

// ============================================================================
//  Tab content builders (one per agent)
// ============================================================================

function factsTab(analysis) {
  const parties = (analysis.parties || []).map((p) =>
    typeof p === "object" ? `${p.name}${p.role ? ` — ${p.role}` : ""}` : String(p));
  return el("div", {}, [
    ListBlock("Facts", analysis.facts),
    ListBlock("Legal issues", analysis.legal_issues),
    ListBlock("Possible charges", analysis.possible_charges),
    ListBlock("Parties", parties),
    ListBlock("Evidence identified", analysis.evidence),
    ExplainBlock(analysis),
  ]);
}

function prosecutionTab(prosecution) {
  return el("div", {}, [
    ListBlock("Prosecution arguments", prosecution.arguments),
    ListBlock("Supporting evidence", prosecution.supporting_evidence),
    TextBlock("Why the charges may apply", prosecution.why_charges_apply),
    ListBlock("Legal references", prosecution.legal_references),
    ExplainBlock(prosecution),
  ]);
}

function defenseTab(defense) {
  return el("div", {}, [
    ListBlock("Defense arguments", defense.arguments),
    ListBlock("Weaknesses in the prosecution case", defense.prosecution_weaknesses),
    ListBlock("Procedural issues to examine", defense.procedural_issues),
    ListBlock("Alternative interpretations", defense.alternative_interpretations),
    ListBlock("Legal references", defense.legal_references),
    ExplainBlock(defense),
  ]);
}

function evidenceTab(review) {
  const assessments = (review.evidence_assessments || []).map((a) =>
    el("div", { class: "mb-2 rounded-xl border border-slate-200 p-3 dark:border-slate-800" }, [
      el("div", { class: "mb-1 flex items-center justify-between gap-2" }, [
        el("span", { class: "min-w-0 flex-1 truncate text-sm font-semibold text-slate-800 dark:text-slate-100", title: a.evidence }, a.evidence),
        StrengthBadge(a.strength),
      ]),
      el("p", { class: "text-sm leading-relaxed text-slate-600 dark:text-slate-300" }, a.reasoning || ""),
    ]));

  return el("div", {}, [
    el("div", { class: "mb-4 flex items-center gap-2" }, [
      el("span", { class: "text-sm font-bold text-slate-800 dark:text-slate-100" }, "Overall evidence strength:"),
      StrengthBadge(review.overall_strength),
    ]),
    el("div", { class: "mb-4" }, [
      el("h4", { class: "mb-1.5 text-sm font-bold text-slate-800 dark:text-slate-100" }, "Item-by-item assessment"),
      assessments.length ? el("div", {}, assessments) : el("p", { class: "text-sm text-slate-400 dark:text-slate-500" }, "No individual assessments."),
    ]),
    TextBlock("Witness reliability", review.witness_reliability),
    TextBlock("Document quality", review.document_quality),
    TextBlock("Digital evidence quality", review.digital_evidence_quality),
    ListBlock("Chain-of-custody concerns", review.chain_of_custody_concerns),
    ExplainBlock(review),
  ]);
}

function judgeTab(judge) {
  return el("div", {}, [
    TextBlock("Legal reasoning", judge.legal_reasoning),
    ListBlock("Findings", judge.findings),
    TextBlock("Verdict reasoning", judge.verdict_reasoning),
    ExplainBlock(judge),
  ]);
}

// ============================================================================
//  Results rendering (verdict banner + tabs + export)
// ============================================================================

function VerdictBanner(report) {
  const verdict = report.judge?.verdict || "Uncertain Outcome";
  const tones = {
    "Likely Conviction": "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-200",
    "Uncertain Outcome": "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200",
    "Likely Acquittal": "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-200",
  };

  const exportBtn = el("button", {
    type: "button",
    class: "inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 focus-ring disabled:opacity-60",
    onClick: async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.innerHTML = '<span class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"></span><span>Preparing…</span>';
      try {
        const blob = await fetchCaseReportPdf(report);
        const url = URL.createObjectURL(blob);
        const a = el("a", { href: url, download: `${report.case?.title || "case"} - simulation report.pdf` });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
        btn.innerHTML = icon("check", "w-3.5 h-3.5") + "<span>Downloaded</span>";
      } catch {
        btn.innerHTML = icon("alert", "w-3.5 h-3.5") + "<span>Export failed — retry</span>";
      } finally {
        btn.disabled = false;
        setTimeout(() => (btn.innerHTML = icon("download", "w-3.5 h-3.5") + "<span>Export PDF</span>"), 2500);
      }
    },
    html: icon("download", "w-3.5 h-3.5") + "<span>Export PDF</span>",
  });

  return el("div", { class: `mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border p-4 ${tones[verdict] || tones["Uncertain Outcome"]}` }, [
    el("div", { class: "flex items-center gap-3" }, [
      el("span", { class: "flex h-10 w-10 items-center justify-center rounded-xl bg-white/60 dark:bg-black/20", html: icon("scale", "w-5 h-5") }),
      el("div", {}, [
        el("p", { class: "text-[11px] font-semibold uppercase tracking-wider opacity-70" }, "Simulated verdict tendency"),
        el("p", { class: "font-display text-lg font-semibold leading-tight" }, verdict),
      ]),
    ]),
    el("div", { class: "flex items-center gap-2" }, [
      el("span", { class: "text-xs font-semibold opacity-80" }, `Judge confidence: ${report.judge?.confidence ?? 0}/100`),
      exportBtn,
    ]),
  ]);
}

function renderReport(report) {
  clear(els.results);
  lastReport = report;

  const TABS = [
    { id: "facts", label: "Facts", icon: "search", build: () => factsTab(report.analysis || {}) },
    { id: "prosecution", label: "Prosecution", icon: "gavel", build: () => prosecutionTab(report.prosecution || {}) },
    { id: "defense", label: "Defense", icon: "shield", build: () => defenseTab(report.defense || {}) },
    { id: "evidence", label: "Evidence Review", icon: "scroll", build: () => evidenceTab(report.evidence_review || {}) },
    { id: "judge", label: "Judge Decision", icon: "scale", build: () => judgeTab(report.judge || {}) },
  ];

  let active = "facts";
  const panel = el("div", { class: "rounded-b-2xl rounded-tr-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900", role: "tabpanel", id: "sim-panel", tabindex: "0" });
  const tablist = el("div", { class: "flex flex-wrap gap-1", role: "tablist", "aria-label": "Simulation results" });

  const renderTabs = () => {
    clear(tablist);
    TABS.forEach((t) => {
      const selected = t.id === active;
      tablist.append(el("button", {
        type: "button",
        role: "tab",
        id: `tab-${t.id}`,
        "aria-selected": selected ? "true" : "false",
        "aria-controls": "sim-panel",
        tabindex: selected ? "0" : "-1",
        class:
          "inline-flex items-center gap-1.5 rounded-t-xl border border-b-0 px-3.5 py-2.5 text-sm font-semibold transition-colors focus-ring " +
          (selected
            ? "border-slate-200 bg-white text-brand-700 dark:border-slate-800 dark:bg-slate-900 dark:text-brand-300"
            : "border-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/70 dark:hover:text-slate-200"),
        onClick: () => { active = t.id; renderTabs(); renderPanel(); },
        onKeydown: (e) => {
          const idx = TABS.findIndex((x) => x.id === active);
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            const next = (idx + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
            active = TABS[next].id;
            renderTabs(); renderPanel();
            tablist.querySelector(`#tab-${active}`)?.focus();
          }
        },
        html: icon(t.icon, "w-4 h-4") + `<span>${t.label}</span>`,
      }));
    });
  };

  const renderPanel = () => {
    clear(panel);
    panel.setAttribute("aria-labelledby", `tab-${active}`);
    panel.append(TABS.find((t) => t.id === active).build());
  };

  renderTabs();
  renderPanel();

  els.results.append(
    el("div", { class: "anim-rise" }, [
      VerdictBanner(report),
      tablist,
      panel,
      el("p", { class: "mt-4 rounded-lg bg-slate-100 p-3 text-[11px] leading-relaxed text-slate-500 dark:bg-slate-800/60 dark:text-slate-400" },
        report.disclaimer || ""),
    ])
  );
}

// ============================================================================
//  Form & run flow
// ============================================================================

function setRunBusy(busy) {
  els.runBtn.disabled = busy;
  els.runBtn.innerHTML = busy
    ? '<span class="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"></span><span>Simulating…</span>'
    : icon("play", "w-4 h-4") + "<span>Run simulation</span>";
}

async function runSimulation() {
  if (running) return;
  const payload = {
    title: els.title.value.trim(),
    description: els.description.value.trim(),
    jurisdiction: els.jurisdiction.value.trim() || "Nepal",
    case_type: els.type.value,
  };
  if (payload.title.length < 3 || payload.description.length < 30) {
    els.form.reportValidity();
    return;
  }

  running = true;
  setRunBusy(true);
  clear(els.results);
  const loading = LoadingState();
  els.results.append(loading);

  try {
    const report = await simulateCase(payload);
    loading._cleanup?.();
    renderReport(report);
    try {
      localStorage.setItem(LAST_KEY, JSON.stringify({ input: payload, report }));
    } catch { /* storage full — fine */ }
  } catch (err) {
    loading._cleanup?.();
    clear(els.results);
    const message = err instanceof ApiError ? err.message : "An unexpected error occurred. Please try again.";
    els.results.append(ErrorState(message, runSimulation));
  } finally {
    running = false;
    setRunBusy(false);
  }
}

function updateCharCount() {
  els.charCount.textContent = `${els.description.value.length} / 8000`;
}

function restoreLast() {
  try {
    const raw = localStorage.getItem(LAST_KEY);
    if (!raw) return false;
    const { input, report } = JSON.parse(raw);
    if (input) {
      els.title.value = input.title || "";
      els.description.value = input.description || "";
      els.jurisdiction.value = input.jurisdiction || "Nepal";
      if (input.case_type) els.type.value = input.case_type;
    }
    if (report) {
      renderReport(report);
      return true;
    }
  } catch { /* corrupt cache — ignore */ }
  return false;
}

function init() {
  initTheme();
  bindThemeToggles();
  setRunBusy(false);
  updateCharCount();

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSimulation();
  });
  els.description.addEventListener("input", updateCharCount);
  els.loadExample.addEventListener("click", () => {
    els.title.value = EXAMPLE.title;
    els.type.value = EXAMPLE.type;
    els.jurisdiction.value = EXAMPLE.jurisdiction;
    els.description.value = EXAMPLE.description;
    updateCharCount();
    els.title.focus();
  });

  if (!restoreLast()) {
    els.results.append(EmptyState());
  }
}

init();
