// app.js — application orchestrator: state, send flow, streaming, responsive
// drawers, theme, and accessibility wiring. Composes the reusable components.

import { qs, clear, el } from "./dom.js";
import { icon } from "./icons.js";
import { initTheme, bindThemeToggles } from "./theme.js";
import { ARENA_ORDER, arenaMeta, parseAnswer, deriveGrounding } from "./format.js";
import { sendChat, translateText, ApiError } from "./api.js";
import { ConversationStore } from "./store.js";
import { renderHistory } from "./components/sidebar.js";
import { LandingState } from "./components/states.js";
import {
  UserMessage, AssistantMessage, LoadingMessage, ErrorMessage, streamInto,
} from "./components/message.js";
import { SourcePanelContent } from "./components/citations.js";

const store = new ConversationStore();
let sending = false;
let lastUserMessage = null; // for retry

// --- Element handles --------------------------------------------------------
const els = {
  thread: qs("#thread"),
  history: qs("#history"),
  composer: qs("#composer"),
  input: qs("#input"),
  send: qs("#send"),
  arena: qs("#arena"),
  headerArena: qs("#headerArena"),
  sourceContent: qs("#sourceContent"),
  live: qs("#liveRegion"),
  sidebar: qs("#sidebar"),
  sourcePanel: qs("#sourcePanel"),
  overlay: qs("#overlay"),
  langSetting: qs("#langSetting"),
};

// ============================================================================
//  Bilingual answers (translate-on-demand + default-language setting)
// ============================================================================
const LANG_KEY = "ai-advocate/answer-lang"; // 'ne' | 'en'

function getDefaultLang() {
  return localStorage.getItem(LANG_KEY) === "en" ? "en" : "ne";
}
function setDefaultLang(lang) {
  localStorage.setItem(LANG_KEY, lang === "en" ? "en" : "ne");
  renderLangSetting();
}

/** Settings control: default answer language (radio-style segmented). */
function renderLangSetting() {
  if (!els.langSetting) return;
  clear(els.langSetting);
  const current = getDefaultLang();
  const opt = (value, label) =>
    el("button", {
      type: "button",
      role: "radio",
      "aria-checked": current === value ? "true" : "false",
      class:
        "flex-1 rounded-md px-2 py-1.5 text-xs font-semibold transition-colors focus-ring " +
        (current === value
          ? "bg-brand-600 text-white shadow-sm"
          : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"),
      onClick: () => setDefaultLang(value),
    }, label);

  els.langSetting.append(
    el("div", {
      class: "flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 dark:border-slate-700 dark:bg-slate-900",
      role: "radiogroup",
      "aria-label": "Default answer language",
    }, [opt("ne", "नेपाली"), opt("en", "English")])
  );
}

// Shared bilingual callbacks for every assistant message. Translation is a
// pure post-processing call on the generated Nepali answer (msg.text) — it
// never re-runs retrieval or generates a new answer.
const assistantOpts = {
  onTranslate: async (msg) => await translateText(msg.text),
  onPersist: () => store.save(),
};

// ============================================================================
//  Arena selector
// ============================================================================
function populateArenaSelect() {
  clear(els.arena);
  for (const key of ARENA_ORDER) {
    els.arena.append(el("option", { value: key }, arenaMeta(key).label + (key === "All (auto)" ? "" : "")));
  }
  els.arena.value = "All (auto)";
}

// ============================================================================
//  Rendering
// ============================================================================
function announce(text) {
  if (els.live) els.live.textContent = text;
}

function scrollThread() {
  els.thread.scrollTo({ top: els.thread.scrollHeight, behavior: "smooth" });
}

/** Render the active conversation (or the landing state). */
function renderThread() {
  clear(els.thread);
  const conv = store.active;

  if (!conv || conv.messages.length === 0) {
    els.thread.classList.remove("has-messages");
    els.thread.append(LandingState(handleSuggestion));
    updateSourcePanel(null);
    updateHeaderArena(null);
    return;
  }

  els.thread.classList.add("has-messages");
  const wrap = el("div", { class: "mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6" });
  for (const msg of conv.messages) wrap.append(renderMessage(msg));
  els.thread.append(wrap);

  const latest = store.latestAssistant();
  updateSourcePanel(latest || null);
  updateHeaderArena(latest?.arena || null);
  requestAnimationFrame(scrollThread);
}

function renderMessage(msg) {
  if (msg.role === "user") return UserMessage(msg);
  if (msg.role === "error") return ErrorMessage({ message: msg.text, onRetry: msg.canRetry ? retryLast : null });
  // assistantOpts restores cached translations + language toggle on re-render.
  return AssistantMessage(msg, assistantOpts).root;
}

function updateHeaderArena(arena) {
  clear(els.headerArena);
  if (!arena || arena === "All (auto)") return;
  const meta = arenaMeta(arena);
  els.headerArena.append(
    el("span", {
      class: "inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300",
    }, [el("span", { class: "text-brand-500", html: icon(meta.icon, "w-3.5 h-3.5") }), meta.label])
  );
}

function updateSourcePanel(assistantMsg) {
  clear(els.sourceContent);
  if (!assistantMsg) {
    els.sourceContent.append(SourcePanelContent(null));
    return;
  }
  els.sourceContent.append(
    SourcePanelContent({
      uniqueCitations: assistantMsg.uniqueCitations || [],
      grounding: assistantMsg.grounding,
    })
  );
}

function renderSidebar() {
  renderHistory(els.history, store.conversations, store.activeId, {
    onSelect: (id) => {
      store.setActive(id);
      renderSidebar();
      renderThread();
      closeDrawers();
    },
    onDelete: (id) => {
      store.remove(id);
      renderSidebar();
      renderThread();
    },
  });
}

// ============================================================================
//  Send flow
// ============================================================================
async function handleSend(text, arenaOverride) {
  const message = (text ?? els.input.value).trim();
  if (!message || sending) return;

  const arena = arenaOverride || els.arena.value || "All (auto)";
  lastUserMessage = { message, arena };

  // Ensure an active conversation exists.
  if (!store.active) store.create();
  const convId = store.activeId;

  store.addMessage(convId, { role: "user", text: message });
  resetInput();
  renderSidebar();
  renderThread();
  announce("Message sent. Searching legal sources.");

  // Loading state.
  sending = true;
  setComposerBusy(true);
  const loading = LoadingMessage();
  threadWrap().append(loading);
  scrollThread();

  try {
    const data = await sendChat(message, arena);
    const { text: cleanText, uniqueCitations } = parseAnswer(data.answer);
    const grounding = deriveGrounding({ ok: data.ok, citations: uniqueCitations, text: cleanText });

    const saved = store.addMessage(convId, {
      role: "assistant",
      text: cleanText,
      arena: data.detected_arena,
      uniqueCitations,
      grounding,
      // Trust/verification block from the backend's output guard: how many of
      // this answer's citations were substantiated against the real corpus.
      security: data.security || null,
    });

    // Swap the loading bubble for a streaming assistant message.
    const view = AssistantMessage(saved, { animate: true, ...assistantOpts });
    loading.replaceWith(view.root);
    updateHeaderArena(data.detected_arena);

    await streamInto(view.answerEl, cleanText, { onTick: () => maybeStickScroll() });
    // Re-render body via the component (respects any language switch that
    // happened mid-stream), then mount citations.
    view.refreshBody();
    if (uniqueCitations.length) {
      const { CitationList } = await import("./components/citations.js");
      view.extrasEl.append(CitationList(uniqueCitations));
    }
    updateSourcePanel(saved);
    announce(`Answer ready. Grounding: ${grounding.label}.`);
    scrollThread();

    // Settings: "Default answer language = English" → generate Nepali first
    // (above), then auto-translate the finished answer. Citations are
    // preserved verbatim by the translator; failure keeps Nepali visible.
    if (getDefaultLang() === "en") {
      const switched = await view.setLang("en");
      if (switched) announce("Answer translated to English.");
      scrollThread();
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    const message = err instanceof ApiError ? err.message : "An unexpected error occurred. Please try again.";
    store.addMessage(convId, { role: "error", text: message, canRetry: true });
    loading.replaceWith(ErrorMessage({ message, onRetry: retryLast }));
    announce("The request failed.");
  } finally {
    sending = false;
    setComposerBusy(false);
    els.input.focus();
  }
}

function retryLast() {
  if (!lastUserMessage || sending) return;
  // Remove the trailing error message before retrying.
  const conv = store.active;
  if (conv && conv.messages.at(-1)?.role === "error") conv.messages.pop();
  renderThread();
  handleSend(lastUserMessage.message, lastUserMessage.arena);
}

function handleSuggestion(question, arena) {
  els.arena.value = arena;
  handleSend(question, arena);
}

// ============================================================================
//  Composer helpers
// ============================================================================
function threadWrap() {
  let wrap = els.thread.querySelector(".max-w-3xl");
  if (!wrap) {
    wrap = el("div", { class: "mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6" });
    clear(els.thread);
    els.thread.append(wrap);
    els.thread.classList.add("has-messages");
  }
  return wrap;
}

function maybeStickScroll() {
  const nearBottom =
    els.thread.scrollHeight - els.thread.scrollTop - els.thread.clientHeight < 160;
  if (nearBottom) els.thread.scrollTop = els.thread.scrollHeight;
}

function resetInput() {
  els.input.value = "";
  autoGrow();
}
function autoGrow() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 180) + "px";
}
function setComposerBusy(busy) {
  els.send.disabled = busy;
  els.input.setAttribute("aria-busy", busy ? "true" : "false");
  els.send.innerHTML = busy
    ? '<span class="h-5 w-5 animate-spin rounded-full border-2 border-white/40 border-t-white"></span>'
    : icon("send", "w-5 h-5");
}

// ============================================================================
//  Responsive drawers
// ============================================================================
function openSidebar() {
  els.sidebar.classList.remove("-translate-x-full");
  showOverlay();
}
function openSourcePanel() {
  els.sourcePanel.classList.remove("translate-x-full");
  showOverlay();
}
function closeDrawers() {
  els.sidebar.classList.add("-translate-x-full");
  els.sourcePanel.classList.add("translate-x-full");
  hideOverlay();
}
function showOverlay() { els.overlay.classList.remove("hidden"); }
function hideOverlay() { els.overlay.classList.add("hidden"); }

// ============================================================================
//  Init
// ============================================================================
function bindEvents() {
  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSend();
  });
  els.input.addEventListener("input", autoGrow);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  qs("#newChat").addEventListener("click", () => {
    store.create();
    renderSidebar();
    renderThread();
    closeDrawers();
    els.input.focus();
  });

  qs("#clearAll").addEventListener("click", () => {
    if (confirm("Delete all saved consultations? This cannot be undone.")) {
      store.clearAll();
      renderSidebar();
      renderThread();
    }
  });

  qs("#menuBtn").addEventListener("click", openSidebar);
  qs("#sourceToggle").addEventListener("click", openSourcePanel);
  qs("#panelClose").addEventListener("click", closeDrawers);
  els.overlay.addEventListener("click", closeDrawers);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawers();
  });
}

function init() {
  initTheme();
  bindThemeToggles();
  populateArenaSelect();
  renderLangSetting();
  bindEvents();
  renderSidebar();
  renderThread();
  autoGrow();
}

init();
