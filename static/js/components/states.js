// components/states.js — landing (empty) state, suggested questions, and
// small empty placeholders.

import { el } from "../dom.js";
import { icon } from "../icons.js";
import { arenaMeta, toneClasses } from "../format.js";

// Curated legal starter questions, each tagged to its Act (focus area).
export const SUGGESTED_QUESTIONS = [
  { q: "How do I register a pharmacy business in Nepal?", arena: "Pharmacy Act" },
  { q: "What does the Constitution say about the right to health?", arena: "Constitution of Nepal" },
  { q: "Who is responsible for running the immunization programme?", arena: "Immunization Act" },
  { q: "What rights and protections do single women have?", arena: "Single Women Act" },
  { q: "What provisions exist for sports development?", arena: "Sports Act" },
  { q: "How can a person obtain Nepali citizenship?", arena: "Constitution of Nepal" },
];

/**
 * A suggested-question card.
 * @param {{q,arena}} item
 * @param {(q:string, arena:string)=>void} onPick
 */
function SuggestionCard(item, onPick, index) {
  const meta = arenaMeta(item.arena);
  return el("button", {
    type: "button",
    class:
      "group flex w-full items-start gap-3 rounded-2xl border border-slate-200 bg-white p-4 text-left " +
      "transition-all duration-200 hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-md focus-ring " +
      "dark:border-slate-800 dark:bg-slate-900/60 dark:hover:border-brand-700 anim-rise",
    style: `animation-delay:${Math.min(index * 70, 400)}ms`,
    onClick: () => onPick(item.q, item.arena),
  }, [
    el("span", {
      class: "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ring-inset " + toneClasses(meta.tone),
      html: icon(meta.icon, "w-5 h-5"),
    }),
    el("span", { class: "min-w-0 flex-1" }, [
      el("span", { class: "block text-[15px] font-medium leading-snug text-slate-800 dark:text-slate-100" }, item.q),
      el("span", { class: "mt-1.5 inline-block text-xs font-medium text-slate-400 dark:text-slate-500" }, meta.label),
    ]),
    el("span", {
      class: "mt-0.5 shrink-0 text-slate-300 transition-all group-hover:translate-x-0.5 group-hover:text-brand-500 dark:text-slate-600",
      html: icon("send", "w-4 h-4"),
    }),
  ]);
}

/** Grid of suggested questions. */
export function SuggestedQuestions(onPick) {
  return el("div", { class: "grid gap-3 sm:grid-cols-2" },
    SUGGESTED_QUESTIONS.map((item, i) => SuggestionCard(item, onPick, i)));
}

/** Landing / empty-conversation hero shown when a chat has no messages. */
export function LandingState(onPick) {
  return el("div", { class: "mx-auto flex min-h-full max-w-3xl flex-col justify-center px-4 py-10" }, [
    // Brand mark + headline
    el("div", { class: "mb-8 text-center anim-rise" }, [
      el("div", {
        class: "mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-600 to-brand-800 text-white shadow-lg shadow-brand-600/20",
        html: icon("scale", "w-8 h-8"),
      }),
      el("h1", { class: "font-display text-3xl font-semibold tracking-tight text-slate-900 dark:text-white sm:text-4xl" },
        "Nepali legal guidance, grounded in the law"),
      el("p", { class: "mx-auto mt-3 max-w-xl text-[15px] leading-relaxed text-slate-500 dark:text-slate-400" },
        "Ask about the Constitution, Pharmacy, Immunization, Single Women, and Sports Acts. Every answer is retrieved from and cited to the official statutes."),
    ]),

    // Trust chips
    el("div", { class: "mb-6 flex flex-wrap items-center justify-center gap-2 anim-rise", style: "animation-delay:80ms" }, [
      TrustChip("shield", "Cites official Acts"),
      TrustChip("search", "Hybrid legal retrieval"),
      TrustChip("scroll", "5 statutes indexed"),
    ]),

    // Suggested questions
    el("div", { class: "anim-rise", style: "animation-delay:140ms" }, [
      el("p", { class: "mb-3 text-center text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500" },
        "Try asking"),
      SuggestedQuestions(onPick),
    ]),
  ]);
}

function TrustChip(iconName, label) {
  return el("span", {
    class: "inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white/70 px-3 py-1 text-xs font-medium text-slate-600 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-300",
  }, [
    el("span", { class: "text-brand-500", html: icon(iconName, "w-3.5 h-3.5") }),
    label,
  ]);
}

/** Empty sidebar history placeholder. */
export function EmptyHistory() {
  return el("div", { class: "px-3 py-6 text-center" }, [
    el("p", { class: "text-xs text-slate-400 dark:text-slate-500" }, "No consultations yet."),
    el("p", { class: "mt-1 text-xs text-slate-400 dark:text-slate-500" }, "Start a new one to see it here."),
  ]);
}
