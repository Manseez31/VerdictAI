// format.js — pure presentation logic: arena metadata, answer parsing,
// citation extraction, client-side grounding heuristic, and safe rich-text
// rendering. No DOM construction beyond escaping.

import { escapeHtml } from "./dom.js";

// --- Arena (legal domain) metadata -----------------------------------------
// Keys MUST match the backend's arena strings exactly (do not change).
export const ARENAS = {
  "All (auto)": { label: "Auto-detect", short: "Auto", icon: "sparkles", tone: "slate" },
  "Constitution of Nepal": { label: "Constitution of Nepal", short: "Constitution", icon: "scroll", tone: "indigo" },
  "Pharmacy Act": { label: "Pharmacy Act", short: "Pharmacy", icon: "pill", tone: "emerald" },
  "Immunization Act": { label: "Immunization Act", short: "Immunization", icon: "syringe", tone: "sky" },
  "Single Women Act": { label: "Single Women Act", short: "Single Women", icon: "user", tone: "rose" },
  "Sports Act": { label: "Sports Act", short: "Sports", icon: "trophy", tone: "amber" },
};

export const ARENA_ORDER = [
  "All (auto)", "Constitution of Nepal", "Pharmacy Act",
  "Immunization Act", "Single Women Act", "Sports Act",
];

// Literal (Tailwind-scannable) class bundles per tone, for badges/chips.
const TONE = {
  slate: "bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-400/20",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-600/20 dark:bg-indigo-950 dark:text-indigo-300 dark:ring-indigo-400/25",
  emerald: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/25",
  sky: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-400/25",
  rose: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-400/25",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/25",
};

export function arenaMeta(key) {
  return ARENAS[key] || ARENAS["All (auto)"];
}

export function toneClasses(tone) {
  return TONE[tone] || TONE.slate;
}

// --- Answer parsing ---------------------------------------------------------

/**
 * Split the backend answer into display text + structured citations.
 * The backend embeds `[स्रोत: <act>, धारा <n>]` tags and a trailing
 * `[Detected arena: X]` note; we lift both out for richer UI.
 */
export function parseAnswer(rawAnswer) {
  let text = (rawAnswer || "").trim();

  // Drop the trailing "[Detected arena: X]" note — shown as a badge instead.
  text = text.replace(/\n*\[Detected arena:[^\]]*\]\s*$/u, "").trim();

  // Collect every [स्रोत: ...] citation, in order.
  const citations = [];
  const re = /\[स्रोत:\s*([^\]]+)\]/gu;
  let m;
  while ((m = re.exec(text)) !== null) citations.push(parseCitation(m[1]));

  return { text, citations, uniqueCitations: dedupeCitations(citations) };
}

function parseCitation(rawInner) {
  const raw = rawInner.trim();
  // Section marker is "धारा" (Nepali for "Section/Article").
  const idx = raw.search(/,?\s*धारा\s*/u);
  if (idx >= 0) {
    const act = raw.slice(0, idx).replace(/,\s*$/u, "").trim();
    const section = raw.slice(idx).replace(/^,?\s*धारा\s*/u, "").trim();
    return { act, section, raw };
  }
  return { act: raw, section: null, raw };
}

function dedupeCitations(citations) {
  const seen = new Set();
  const out = [];
  for (const c of citations) {
    const key = `${c.act}|${c.section || ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(c);
  }
  return out;
}

// Map a free-text act name onto a known arena tone (best-effort, for coloring).
export function toneForAct(actName = "") {
  const n = actName.toLowerCase();
  if (n.includes("pharmacy") || n.includes("फार्मेसी")) return "emerald";
  if (n.includes("immun") || n.includes("खोप") || n.includes("प्रतिरक्षण")) return "sky";
  if (n.includes("constitution") || n.includes("संविधान") || n.includes("नेपाल ऐन")) return "indigo";
  if (n.includes("single") || n.includes("एकल") || n.includes("women")) return "rose";
  if (n.includes("sport") || n.includes("खेल")) return "amber";
  return "slate";
}

// --- Grounding heuristic ----------------------------------------------------
// The /chat API does not return a confidence score, so we derive an *answer
// grounding* signal from concrete, honest signals: request success, whether
// the answer cites sources, and whether it used the "not in sources"
// disclaimer. This is labelled as grounding (not model certainty) in the UI.

export function deriveGrounding({ ok, citations, text }) {
  const t = text || "";
  const notFound = /थाहा छैन|फेला परेन/u.test(t);
  const systemError = ok === false || /माफ गर्नुहोस्.*समस्या/u.test(t);
  const n = citations ? citations.length : 0;

  if (systemError) {
    return level("low", "Unavailable", "The service could not complete this request.");
  }
  if (notFound) {
    return level("low", "Not in sources", "The indexed laws don’t clearly answer this.");
  }
  if (n === 0) {
    return level("moderate", "Unverified", "No legal citations were found in this answer.");
  }
  if (n >= 2) {
    return level("high", "Well-grounded", `${n} legal sources cited from official Acts.`);
  }
  return level("moderate", "Partially grounded", "1 legal source cited.");
}

function level(key, label, description) {
  const meta = {
    high: { score: 3, tone: "emerald", dot: "bg-emerald-500" },
    moderate: { score: 2, tone: "amber", dot: "bg-amber-500" },
    low: { score: 1, tone: "rose", dot: "bg-rose-500" },
  }[key];
  return { key, label, description, ...meta };
}

// --- Safe rich-text rendering ----------------------------------------------

/**
 * Render answer text to safe HTML: escape everything first, then apply a few
 * whitelisted transforms (bold, inline source chips, paragraphs/line breaks).
 */
export function renderRichText(text) {
  const escaped = escapeHtml(text || "");

  // Inline [स्रोत: ...] tags → compact source chips.
  let html = escaped.replace(/\[स्रोत:\s*([^\]]+)\]/gu, (_, inner) => {
    const c = parseCitation(inner);
    const labelText = c.section ? `धारा ${c.section}` : truncate(c.act, 22);
    return `<span class="source-chip" title="${escapeHtml(c.raw)}">§ ${escapeHtml(labelText)}</span>`;
  });

  // **bold**
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // Paragraphs on blank lines; single newlines → <br>.
  const blocks = html.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return blocks.map((b) => `<p>${b.replace(/\n/g, "<br>")}</p>`).join("");
}

export function truncate(str, max = 40) {
  const s = (str || "").trim();
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}
