// components/citations.js — legal-reference UI:
//   • CitationCard        — a single cited Act + section
//   • ConfidenceIndicator — answer-grounding meter
//   • SourcePanel         — aggregated sources + grounding for a response

import { el } from "../dom.js";
import { icon } from "../icons.js";
import { toneClasses, toneForAct, truncate } from "../format.js";

/**
 * A citation card for one legal reference.
 * @param {{act:string, section:?string}} citation
 */
export function CitationCard(citation, index = 0) {
  const tone = toneForAct(citation.act);
  return el(
    "article",
    {
      class:
        "group flex items-start gap-3 rounded-xl border border-slate-200/80 bg-white p-3 " +
        "transition-all duration-200 hover:border-brand-300 hover:shadow-sm " +
        "dark:border-slate-800 dark:bg-slate-900/60 dark:hover:border-brand-700 anim-rise",
      style: `animation-delay:${Math.min(index * 60, 300)}ms`,
    },
    [
      el("span", {
        class:
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset " +
          toneClasses(tone),
        html: icon("scale", "w-4 h-4"),
      }),
      el("div", { class: "min-w-0 flex-1" }, [
        el("p", {
          class: "truncate text-sm font-semibold text-slate-800 dark:text-slate-100",
          title: citation.act,
        }, citation.act),
        el(
          "div",
          { class: "mt-1 flex items-center gap-2" },
          citation.section
            ? el("span", {
                class:
                  "inline-flex items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-xs font-medium " +
                  "text-slate-600 dark:bg-slate-800 dark:text-slate-300",
              }, `धारा ${citation.section}`)
            : el("span", { class: "text-xs text-slate-400 dark:text-slate-500" }, "Referenced Act")
        ),
      ]),
    ]
  );
}

/** A row of citation cards below an assistant answer (or null if none). */
export function CitationList(citations) {
  if (!citations || citations.length === 0) return null;
  return el("section", { class: "mt-4", "aria-label": "Citations" }, [
    el("div", { class: "mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, [
      el("span", { html: icon("bookmark", "w-3.5 h-3.5") }),
      "Citations",
    ]),
    el(
      "div",
      { class: "grid gap-2 sm:grid-cols-2" },
      citations.map((c, i) => CitationCard(c, i))
    ),
  ]);
}

/**
 * Answer-grounding indicator (3-segment meter).
 * @param {{key,label,description,tone,dot,score}} grounding
 * @param {boolean} compact
 */
export function ConfidenceIndicator(grounding, { compact = false } = {}) {
  const segs = [1, 2, 3].map((n) =>
    el("span", {
      class:
        "h-1.5 flex-1 rounded-full transition-colors " +
        (n <= grounding.score ? grounding.dot : "bg-slate-200 dark:bg-slate-700"),
    })
  );

  return el(
    "div",
    {
      class:
        "rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/60" +
        (compact ? "" : ""),
      role: "group",
      "aria-label": `Answer grounding: ${grounding.label}`,
    },
    [
      el("div", { class: "flex items-center justify-between" }, [
        el("div", { class: "flex items-center gap-2" }, [
          el("span", { class: `h-2.5 w-2.5 rounded-full ${grounding.dot}` }),
          el("span", { class: "text-sm font-semibold text-slate-800 dark:text-slate-100" }, grounding.label),
        ]),
        el("span", {
          class: "text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500",
          title: "Derived from whether the answer cites official Acts — not a model confidence score.",
        }, "Grounding"),
      ]),
      el("div", { class: "mt-2.5 flex items-center gap-1" }, segs),
      compact ? null : el("p", { class: "mt-2 text-xs leading-relaxed text-slate-500 dark:text-slate-400" }, grounding.description),
    ]
  );
}

/**
 * Right-hand source panel content for a response.
 * @param {{uniqueCitations:Array, grounding:Object}|null} data
 */
export function SourcePanelContent(data) {
  if (!data || !data.grounding) {
    return el("div", { class: "flex flex-col items-center justify-center px-6 py-16 text-center" }, [
      el("span", {
        class: "mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500",
        html: icon("scale", "w-6 h-6"),
      }),
      el("p", { class: "text-sm font-medium text-slate-600 dark:text-slate-300" }, "No sources yet"),
      el("p", { class: "mt-1 max-w-[220px] text-xs leading-relaxed text-slate-400 dark:text-slate-500" },
        "Ask a legal question and the Acts and sections behind the answer will appear here."),
    ]);
  }

  const citations = data.uniqueCitations || [];
  const children = [ConfidenceIndicator(data.grounding)];

  children.push(
    el("div", { class: "mt-5" }, [
      el("div", { class: "mb-2 flex items-center justify-between" }, [
        el("h3", { class: "text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500" }, "Legal sources"),
        el("span", { class: "rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-500 dark:bg-slate-800 dark:text-slate-400" },
          String(citations.length)),
      ]),
      citations.length
        ? el("div", { class: "space-y-2" }, citations.map((c, i) => CitationCard(c, i)))
        : el("p", { class: "rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-slate-400 dark:border-slate-700 dark:text-slate-500" },
            "This answer did not cite specific sections."),
    ])
  );

  // Trust footer.
  children.push(
    el("p", { class: "mt-5 flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-500 dark:bg-slate-800/50 dark:text-slate-400" }, [
      el("span", { class: "mt-px shrink-0 text-slate-400", html: icon("shield", "w-3.5 h-3.5") }),
      "Sources are drawn from official Nepali Acts indexed by VerdictAI. Always verify critical matters with the primary text.",
    ])
  );

  return el("div", { class: "p-4" }, children);
}
