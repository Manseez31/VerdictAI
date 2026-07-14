// dom.js — tiny DOM helpers shared by every component.
// Keeps components declarative without pulling in a framework/build step.

/**
 * Create an element.
 * @param {string} tag
 * @param {Object} props - class, html (innerHTML), dataset, aria-*, on<Event> handlers, or plain attrs/props
 * @param {(Node|string|Array)} children
 */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === "for") {
      node.setAttribute("for", v);
    } else if (k in node && k !== "list" && k !== "type") {
      try { node[k] = v; } catch { node.setAttribute(k, v); }
    } else {
      node.setAttribute(k, v);
    }
  }
  appendChildren(node, children);
  return node;
}

export function appendChildren(node, children) {
  const kids = Array.isArray(children) ? children : [children];
  for (const c of kids) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** Escape untrusted text before it is placed into innerHTML. */
export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

/** Remove all children from a node. */
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Query helper. */
export const qs = (sel, root = document) => root.querySelector(sel);

/** True when the user prefers reduced motion (drives our animation fallbacks). */
export const prefersReducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;
