// components/message.js — chat message bubbles + loading/streaming/error
// states, plus the bilingual (Nepali ⇄ English) answer controls.

import { el, clear, escapeHtml, prefersReducedMotion } from "../dom.js";
import { icon } from "../icons.js";
import { arenaMeta, toneClasses, renderRichText } from "../format.js";
import { CitationList } from "./citations.js";

const AVATAR =
  "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-brand-600 to-brand-800 text-white shadow-sm";

/** User message (right-aligned). */
export function UserMessage(msg) {
  return el("div", { class: "flex w-full justify-end anim-rise" }, [
    el("div", { class: "max-w-[85%] md:max-w-[75%]" }, [
      el("div", {
        class:
          "rounded-2xl rounded-tr-md bg-brand-600 px-4 py-3 text-[15px] leading-relaxed text-white shadow-sm",
      }, [
        el("p", { class: "whitespace-pre-wrap font-deva" }, msg.text),
      ]),
    ]),
  ]);
}

/** Assistant header row (avatar + name + arena badge). */
function assistantHeader(arena) {
  const meta = arenaMeta(arena);
  const badge =
    arena && arena !== "All (auto)"
      ? el("span", {
          class:
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset " +
            toneClasses(meta.tone),
        }, [
          el("span", { html: icon(meta.icon, "w-3 h-3") }),
          meta.label,
        ])
      : null;

  return el("div", { class: "mb-1.5 flex items-center gap-2" }, [
    el("span", { class: "text-sm font-bold text-slate-800 dark:text-slate-100" }, "VerdictAI"),
    badge,
  ]);
}

/**
 * Citation-verification badge (Security & Trust Core).
 *
 * Every [स्रोत: …] tag in an answer is checked against the actual indexed
 * corpus server-side. This surfaces the result so a fabricated statute can
 * never be presented with the same authority as a real one.
 */
function VerificationBadge(security) {
  if (!security || !security.citations_total) return null;

  const { source_trust_score: score, citations_verified: ok, citations_total: total } = security;
  const bad = security.hallucinated_citations || [];

  const tone =
    score >= 100
      ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950/60 dark:text-emerald-300 dark:ring-emerald-400/25"
      : score >= 50
      ? "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950/60 dark:text-amber-300 dark:ring-amber-400/25"
      : "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950/60 dark:text-rose-300 dark:ring-rose-400/25";

  const label =
    score >= 100 ? "All citations verified" : `${ok}/${total} citations verified`;

  const title = bad.length
    ? `Could not verify against the legal corpus: ${bad.join("; ")}`
    : "Every cited Act and section was matched against the indexed legal corpus.";

  return el("span", {
    class: `inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${tone}`,
    title,
    "aria-label": `${label}. Source trust score ${score} out of 100.`,
  }, [
    el("span", { html: icon(score >= 100 ? "shield" : "alert", "w-3 h-3") }),
    label,
  ]);
}

// Shared class for small action buttons under an answer.
const ACTION_BTN =
  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-400 transition-colors " +
  "hover:bg-slate-100 hover:text-slate-600 focus-ring dark:hover:bg-slate-800 dark:hover:text-slate-300";

/**
 * Assistant message with bilingual controls.
 *
 * The message object may carry bilingual state that we mutate + persist:
 *   msg.translation — cached English translation (set once, reused forever)
 *   msg.lang        — currently displayed language: 'ne' (default) | 'en'
 *
 * Returns { root, answerEl, extrasEl, setLang, refreshBody } so the app can
 * stream text into answerEl, mount citations into extrasEl, and auto-switch
 * language (settings: default answer language) via setLang('en').
 *
 * @param {{text,arena,uniqueCitations,grounding,translation?,lang?}} msg
 * @param {{animate?:boolean, onTranslate?:(msg)=>Promise<string>, onPersist?:(msg)=>void}} opts
 */
export function AssistantMessage(msg, { animate = false, onTranslate, onPersist } = {}) {
  const answerEl = el("div", {
    class: "answer-body font-deva text-[15px] leading-[1.7] text-slate-700 dark:text-slate-200",
  });
  const extrasEl = el("div", {});
  const controlsEl = el("div", { class: "mt-1.5 flex flex-wrap items-center gap-2 pl-1" });

  let translating = false;
  let errorNote = null;

  const showingEnglish = () => msg.lang === "en" && !!msg.translation;
  const currentText = () => (showingEnglish() ? msg.translation : msg.text);

  /** Render the answer body in the currently selected language. */
  const renderBody = () => {
    answerEl.classList.toggle("font-deva", !showingEnglish());
    answerEl.innerHTML = renderRichText(currentText());
  };

  const persist = () => {
    try { onPersist && onPersist(msg); } catch { /* persistence is best-effort */ }
  };

  /**
   * Switch the displayed language. Switching to English translates on first
   * use (via onTranslate) and reuses the cached translation afterwards — no
   * repeat API calls, and never a re-retrieval.
   * @returns {Promise<boolean>} whether the switch succeeded
   */
  async function setLang(lang) {
    if (lang !== "en") {
      if (msg.lang !== "ne") { msg.lang = "ne"; persist(); }
      renderBody();
      renderControls();
      return true;
    }
    if (msg.translation) {
      if (msg.lang !== "en") { msg.lang = "en"; persist(); }
      renderBody();
      renderControls();
      return true;
    }
    if (!onTranslate || translating) return false;

    translating = true;
    errorNote = null;
    renderControls();
    try {
      const translated = await onTranslate(msg);
      msg.translation = translated;
      msg.lang = "en";
      persist();
      renderBody();
      return true;
    } catch (err) {
      // Keep the Nepali answer visible; surface a small, non-destructive error.
      errorNote = (err && err.message) || "Translation failed. Please try again.";
      return false;
    } finally {
      translating = false;
      renderControls();
    }
  }

  function copyButton() {
    return el("button", {
      type: "button",
      class: ACTION_BTN,
      "aria-label": "Copy answer",
      onClick: async (e) => {
        try {
          await navigator.clipboard.writeText(currentText() || "");
          const btn = e.currentTarget;
          btn.innerHTML = icon("check", "w-3.5 h-3.5") + "<span>Copied</span>";
          setTimeout(() => (btn.innerHTML = icon("copy", "w-3.5 h-3.5") + "<span>Copy</span>"), 1600);
        } catch { /* clipboard unavailable */ }
      },
      html: icon("copy", "w-3.5 h-3.5") + "<span>Copy</span>",
    });
  }

  /** Segmented "Show Nepali / Show English" toggle (instant, cached). */
  function langToggle() {
    const seg = (label, active, onClick) =>
      el("button", {
        type: "button",
        class:
          "rounded-md px-2 py-1 text-xs font-semibold transition-colors focus-ring " +
          (active
            ? "bg-brand-600 text-white shadow-sm"
            : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"),
        "aria-pressed": active ? "true" : "false",
        onClick,
      }, label);

    return el("div", {
      class: "inline-flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 dark:border-slate-700 dark:bg-slate-900",
      role: "group",
      "aria-label": "Answer language",
    }, [
      seg("Show Nepali", !showingEnglish(), () => setLang("ne")),
      seg("Show English", showingEnglish(), () => setLang("en")),
    ]);
  }

  function renderControls() {
    clear(controlsEl);
    const badge = VerificationBadge(msg.security);
    if (badge) controlsEl.append(badge);
    controlsEl.append(copyButton());

    if (translating) {
      controlsEl.append(
        el("span", {
          class: "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-brand-600 dark:text-brand-300",
          role: "status",
        }, [
          el("span", { class: "h-3 w-3 animate-spin rounded-full border-2 border-brand-400/40 border-t-brand-500" }),
          "Translating…",
        ])
      );
    } else if (msg.translation) {
      controlsEl.append(langToggle());
    } else if (onTranslate) {
      controlsEl.append(
        el("button", {
          type: "button",
          class: ACTION_BTN,
          "aria-label": "Translate answer to English",
          onClick: () => setLang("en"),
          html: icon("globe", "w-3.5 h-3.5") + "<span>Translate to English</span>",
        })
      );
    }

    if (errorNote) {
      controlsEl.append(
        el("span", { class: "text-xs text-rose-500 dark:text-rose-400", role: "alert" }, errorNote)
      );
    }
  }

  if (!animate) {
    renderBody();
    if (msg.uniqueCitations?.length) extrasEl.append(CitationList(msg.uniqueCitations));
  }
  renderControls();

  const root = el("div", { class: "flex w-full gap-3 anim-rise" }, [
    el("div", { class: AVATAR, html: icon("scale", "w-5 h-5") }),
    el("div", { class: "min-w-0 flex-1" }, [
      assistantHeader(msg.arena),
      el("div", {
        class:
          "rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3.5 shadow-sm " +
          "dark:border-slate-800 dark:bg-slate-900",
      }, [answerEl, extrasEl]),
      controlsEl,
    ]),
  ]);

  return { root, answerEl, extrasEl, setLang, refreshBody: renderBody };
}

/**
 * Loading ("thinking") bubble shown while awaiting the response.
 * Includes an animated status line + shimmer skeleton.
 */
export function LoadingMessage() {
  const dots = el("span", { class: "flex items-center gap-1" },
    [0, 1, 2].map((i) =>
      el("span", {
        class: "h-1.5 w-1.5 rounded-full bg-brand-500 loading-dot",
        style: `animation-delay:${i * 160}ms`,
      })
    )
  );

  const skeleton = el("div", { class: "mt-3 space-y-2" }, [
    el("div", { class: "h-3 w-11/12 rounded shimmer" }),
    el("div", { class: "h-3 w-4/5 rounded shimmer" }),
    el("div", { class: "h-3 w-2/3 rounded shimmer" }),
  ]);

  return el("div", { class: "flex w-full gap-3 anim-rise", role: "status", "aria-label": "Searching legal sources" }, [
    el("div", { class: AVATAR, html: icon("scale", "w-5 h-5") }),
    el("div", { class: "min-w-0 flex-1" }, [
      el("div", { class: "mb-1.5 flex items-center gap-2" }, [
        el("span", { class: "text-sm font-bold text-slate-800 dark:text-slate-100" }, "VerdictAI"),
      ]),
      el("div", {
        class: "rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3.5 shadow-sm dark:border-slate-800 dark:bg-slate-900",
      }, [
        el("div", { class: "flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" }, [
          dots,
          el("span", { class: "font-medium" }, "Searching the Acts…"),
        ]),
        skeleton,
      ]),
    ]),
  ]);
}

/** Error bubble with a retry affordance. */
export function ErrorMessage({ message, onRetry }) {
  return el("div", { class: "flex w-full gap-3 anim-rise", role: "alert" }, [
    el("div", {
      class: "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-rose-100 text-rose-600 dark:bg-rose-950 dark:text-rose-300",
      html: icon("alert", "w-5 h-5"),
    }),
    el("div", { class: "min-w-0 flex-1" }, [
      el("div", {
        class: "rounded-2xl rounded-tl-md border border-rose-200 bg-rose-50 px-4 py-3.5 dark:border-rose-900/60 dark:bg-rose-950/40",
      }, [
        el("p", { class: "text-sm font-semibold text-rose-800 dark:text-rose-200" }, "Something went wrong"),
        el("p", { class: "mt-1 text-sm text-rose-700/90 dark:text-rose-300/90" }, message),
        onRetry
          ? el("button", {
              type: "button",
              class:
                "mt-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-semibold text-white " +
                "transition-colors hover:bg-rose-700 focus-ring",
              onClick: onRetry,
              html: icon("refresh", "w-4 h-4") + "<span>Try again</span>",
            })
          : null,
      ]),
    ]),
  ]);
}

/**
 * Progressively reveal `text` into `answerEl` (streaming feel), then swap to
 * rich HTML. Respects prefers-reduced-motion (renders instantly).
 * @returns {Promise<void>}
 */
export function streamInto(answerEl, text, { onTick } = {}) {
  return new Promise((resolve) => {
    if (prefersReducedMotion() || !text) {
      answerEl.innerHTML = renderRichText(text);
      resolve();
      return;
    }
    const caret = '<span class="stream-caret" aria-hidden="true"></span>';
    const words = text.split(/(\s+)/); // keep whitespace tokens
    let i = 0;
    let acc = "";
    const step = () => {
      // Reveal a few tokens per frame for a smooth but quick stream.
      for (let n = 0; n < 3 && i < words.length; n++, i++) acc += words[i];
      answerEl.innerHTML = escapeHtml(acc) + caret;
      onTick && onTick();
      if (i < words.length) {
        requestAnimationFrame(step);
      } else {
        answerEl.innerHTML = renderRichText(text);
        resolve();
      }
    };
    requestAnimationFrame(step);
  });
}
