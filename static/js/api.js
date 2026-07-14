// api.js — the single boundary to the backend /chat endpoint.
// The request/response contract is unchanged: POST { message, arena }
// -> { answer, detected_arena, ok }.

export class ApiError extends Error {
  constructor(message, kind) {
    super(message);
    this.name = "ApiError";
    this.kind = kind; // 'network' | 'rate_limit' | 'server' | 'parse'
  }
}

/**
 * Send a chat message to the backend.
 * @param {string} message
 * @param {string} arena - one of the ARENAS keys
 * @param {{signal?: AbortSignal}} [opts]
 * @returns {Promise<{answer: string, detected_arena: string, ok: boolean}>}
 */
export async function sendChat(message, arena, { signal } = {}) {
  let res;
  try {
    res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, arena }),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(
      "Couldn’t reach the server. Check your connection and try again.",
      "network"
    );
  }

  if (res.status === 429) {
    throw new ApiError(
      "You’re sending messages too quickly. Please wait a moment and try again.",
      "rate_limit"
    );
  }
  if (!res.ok) {
    throw new ApiError(`The server responded with an error (${res.status}).`, "server");
  }

  try {
    return await res.json();
  } catch {
    throw new ApiError("The server returned an unexpected response.", "parse");
  }
}

/**
 * Translate an already-generated Nepali answer into English.
 * Contract: POST /translate { text } -> { translated_text }.
 * This never re-runs retrieval — it only translates the given text.
 * @param {string} text
 * @param {{signal?: AbortSignal}} [opts]
 * @returns {Promise<string>} the English translation
 */
export async function translateText(text, { signal, targetLang = "en" } = {}) {
  let res;
  try {
    res = await fetch("/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, target_lang: targetLang }),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn’t reach the server for translation.", "network");
  }

  if (res.status === 429) {
    throw new ApiError("Too many requests — please wait a moment and try again.", "rate_limit");
  }
  if (!res.ok) {
    throw new ApiError("Translation failed on the server. Please try again.", "server");
  }

  let data;
  try {
    data = await res.json();
  } catch {
    throw new ApiError("The server returned an unexpected translation response.", "parse");
  }
  if (typeof data.translated_text !== "string" || !data.translated_text.trim()) {
    throw new ApiError("The server returned an empty translation.", "parse");
  }
  return data.translated_text;
}

/**
 * Run the multi-agent Legal Case Simulator.
 * Contract: POST /simulate-case { title, description, jurisdiction, case_type }
 * -> full simulation report. Slow (5 sequential agents, ~20-60s).
 */
export async function simulateCase(payload, { signal } = {}) {
  let res;
  try {
    res = await fetch("/simulate-case", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn’t reach the server. Check your connection and try again.", "network");
  }

  if (res.status === 429) {
    throw new ApiError("You’re running simulations too quickly. Please wait a moment.", "rate_limit");
  }
  if (res.status === 422 || res.status === 413) {
    let detail = "Please check the case details.";
    try { detail = (await res.json()).detail || detail; } catch { /* keep default */ }
    throw new ApiError(detail, "validation");
  }
  if (!res.ok) {
    throw new ApiError("The case simulation failed on the server. Please try again.", "server");
  }

  try {
    return await res.json();
  } catch {
    throw new ApiError("The server returned an unexpected simulation response.", "parse");
  }
}

/**
 * Render an existing simulation report to PDF (agents are NOT re-run).
 * @returns {Promise<Blob>} the PDF blob
 */
export async function fetchCaseReportPdf(report, { signal } = {}) {
  let res;
  try {
    res = await fetch("/simulate-case/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(report),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn’t reach the server for the PDF export.", "network");
  }
  if (!res.ok) {
    throw new ApiError("PDF generation failed on the server.", "server");
  }
  return await res.blob();
}

// ---- Legal Case Intelligence Suite ----

/** Run the full multi-agent Case Intelligence Suite (slow: 8 agents). */
export async function runCaseIntelligence(payload, { signal } = {}) {
  let res;
  try {
    res = await fetch("/case-intelligence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn’t reach the server. Check your connection and try again.", "network");
  }
  if (res.status === 429) throw new ApiError("You’re running analyses too quickly. Please wait a moment.", "rate_limit");
  if (res.status === 422 || res.status === 413) {
    let detail = "Please check the case details.";
    try { detail = (await res.json()).detail || detail; } catch { /* keep default */ }
    throw new ApiError(detail, "validation");
  }
  if (!res.ok) throw new ApiError("The analysis failed on the server. Please try again.", "server");
  try { return await res.json(); } catch { throw new ApiError("Unexpected server response.", "parse"); }
}

/** Fetch the educational demo scenarios. */
export async function fetchDemoCases() {
  const res = await fetch("/case-intelligence/demos");
  if (!res.ok) throw new ApiError("Could not load demo cases.", "server");
  return (await res.json()).cases || [];
}

/** Upload a PDF/DOCX/TXT and get its extracted text (no LLM). */
export async function extractDocument(file, { signal } = {}) {
  const form = new FormData();
  form.append("file", file);
  let res;
  try {
    res = await fetch("/extract-document", { method: "POST", body: form, signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn’t upload the file. Check your connection.", "network");
  }
  if (res.status === 415) throw new ApiError("Unsupported file type. Use PDF, DOCX, or TXT.", "validation");
  if (res.status === 413) throw new ApiError("That file is too large (max 10 MB).", "validation");
  if (!res.ok) {
    let detail = "Could not read text from that file.";
    try { detail = (await res.json()).detail || detail; } catch { /* keep default */ }
    throw new ApiError(detail, "validation");
  }
  return await res.json();
}
