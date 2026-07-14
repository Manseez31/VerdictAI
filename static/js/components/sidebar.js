// components/sidebar.js — conversation history list rendering.

import { el, clear } from "../dom.js";
import { icon } from "../icons.js";
import { EmptyHistory } from "./states.js";

/**
 * A single conversation row.
 * @param {Object} conv
 * @param {boolean} active
 * @param {{onSelect,onDelete}} handlers
 */
function ConversationItem(conv, active, { onSelect, onDelete }) {
  const row = el("div", {
    class:
      "group relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 cursor-pointer transition-colors " +
      (active
        ? "bg-brand-50 text-brand-900 dark:bg-brand-950/60 dark:text-brand-100"
        : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800/70"),
    role: "option",
    "aria-selected": active ? "true" : "false",
    tabindex: "0",
    onClick: () => onSelect(conv.id),
    onKeydown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect(conv.id);
      }
    },
  }, [
    el("span", {
      class: "shrink-0 " + (active ? "text-brand-500" : "text-slate-400 dark:text-slate-500"),
      html: icon("chat", "w-4 h-4"),
    }),
    el("span", { class: "min-w-0 flex-1 truncate text-sm font-medium" }, conv.title),
    el("button", {
      type: "button",
      class:
        "shrink-0 rounded-md p-1 text-slate-400 opacity-0 transition-all hover:bg-slate-200 hover:text-rose-600 " +
        "focus-ring group-hover:opacity-100 dark:hover:bg-slate-700 dark:hover:text-rose-400",
      "aria-label": `Delete “${conv.title}”`,
      onClick: (e) => {
        e.stopPropagation();
        onDelete(conv.id);
      },
      html: icon("trash", "w-3.5 h-3.5"),
    }),
  ]);
  return row;
}

/**
 * (Re)render the history list into `container`.
 * @param {HTMLElement} container
 * @param {Array} conversations
 * @param {string|null} activeId
 * @param {{onSelect,onDelete}} handlers
 */
export function renderHistory(container, conversations, activeId, handlers) {
  clear(container);
  if (!conversations.length) {
    container.append(EmptyHistory());
    return;
  }
  const list = el("div", { class: "space-y-0.5", role: "listbox", "aria-label": "Conversation history" },
    conversations.map((c) => ConversationItem(c, c.id === activeId, handlers)));
  container.append(list);
}
