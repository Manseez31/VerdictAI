// store.js — conversation state with localStorage persistence.
// Enhances the original in-memory history so conversations survive reloads
// (a superset of the previous behaviour; nothing is removed).

import { truncate } from "./format.js";

const STORAGE_KEY = "ai-advocate/conversations/v1";

export class ConversationStore {
  constructor() {
    this._load();
  }

  _load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      this.state = raw ? JSON.parse(raw) : { conversations: [], activeId: null };
    } catch {
      this.state = { conversations: [], activeId: null };
    }
  }

  _save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.state));
    } catch {
      /* storage full / unavailable — keep working in-memory */
    }
  }

  /**
   * Public persistence hook. Components mutate a message object they already
   * hold (e.g. attach a cached translation / language preference) and call
   * this to flush the store — translations therefore survive reloads.
   */
  save() {
    this._save();
  }

  get conversations() {
    return this.state.conversations;
  }

  get activeId() {
    return this.state.activeId;
  }

  get active() {
    return this.conversations.find((c) => c.id === this.activeId) || null;
  }

  create() {
    const conv = {
      id: "conv-" + Date.now(),
      title: "New consultation",
      messages: [],
      createdAt: Date.now(),
    };
    this.state.conversations.unshift(conv);
    this.state.activeId = conv.id;
    this._save();
    return conv;
  }

  setActive(id) {
    this.state.activeId = id;
    this._save();
  }

  addMessage(convId, message) {
    const conv = this.conversations.find((c) => c.id === convId);
    if (!conv) return null;
    const msg = { id: "msg-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6), ts: Date.now(), ...message };
    conv.messages.push(msg);
    // Auto-title from the first user message.
    if (conv.title === "New consultation" && message.role === "user") {
      conv.title = truncate(message.text, 42);
    }
    this._save();
    return msg;
  }

  remove(id) {
    this.state.conversations = this.conversations.filter((c) => c.id !== id);
    if (this.activeId === id) this.state.activeId = this.conversations[0]?.id ?? null;
    this._save();
  }

  clearAll() {
    this.state = { conversations: [], activeId: null };
    this._save();
  }

  /** The most recent assistant message of the active conversation (drives the source panel). */
  latestAssistant(convId = this.activeId) {
    const conv = this.conversations.find((c) => c.id === convId);
    if (!conv) return null;
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === "assistant") return conv.messages[i];
    }
    return null;
  }
}
