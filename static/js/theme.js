// theme.js — shared dark/light theme handling for all pages.
// Buttons opt in with a `data-theme-toggle` attribute.

import { icon } from "./icons.js";

const THEME_KEY = "ai-advocate/theme";

/** Apply the saved (or OS-preferred) theme. Call once on page load. */
export function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", saved ? saved === "dark" : prefersDark);
}

function toggleTheme() {
  const dark = document.documentElement.classList.toggle("dark");
  localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
  syncThemeButtons();
}

function syncThemeButtons() {
  const dark = document.documentElement.classList.contains("dark");
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
    btn.innerHTML = icon(dark ? "sun" : "moon", "w-5 h-5");
    btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
  });
}

/** Wire up every [data-theme-toggle] button (also sets initial icons). */
export function bindThemeToggles() {
  syncThemeButtons();
  document.querySelectorAll("[data-theme-toggle]").forEach((btn) =>
    btn.addEventListener("click", toggleTheme)
  );
}
