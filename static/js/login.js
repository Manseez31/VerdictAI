// login.js — sign in / register.
//
// SECURITY: the access token is held in memory + sessionStorage, never in
// localStorage. The long-lived refresh token is NOT touched by JS at all — it
// lives in an httpOnly, SameSite=Strict cookie the browser manages, so XSS
// cannot exfiltrate it. That is the whole point of the split.

import { qs } from "./dom.js";
import { initTheme } from "./theme.js";
import { setSession, clearSession } from "./session.js";

const els = {
  form: qs("#authForm"),
  tabLogin: qs("#tabLogin"),
  tabRegister: qs("#tabRegister"),
  nameField: qs("#nameField"),
  fullName: qs("#fullName"),
  email: qs("#email"),
  password: qs("#password"),
  pwHint: qs("#pwHint"),
  pwSpacer: qs("#pwSpacer"),
  error: qs("#error"),
  notice: qs("#notice"),
  submit: qs("#submitBtn"),
  forgot: qs("#forgot"),
};

let mode = "login"; // 'login' | 'register'

const ACTIVE = "bg-white text-brand-700 shadow-sm dark:bg-slate-900 dark:text-brand-300";
const INACTIVE = "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200";

function setMode(next) {
  mode = next;
  const isRegister = mode === "register";

  els.tabLogin.className = `rounded-md px-3 py-1.5 text-sm font-semibold transition-colors focus-ring ${isRegister ? INACTIVE : ACTIVE}`;
  els.tabRegister.className = `rounded-md px-3 py-1.5 text-sm font-semibold transition-colors focus-ring ${isRegister ? ACTIVE : INACTIVE}`;
  els.tabLogin.setAttribute("aria-selected", String(!isRegister));
  els.tabRegister.setAttribute("aria-selected", String(isRegister));

  els.nameField.classList.toggle("hidden", !isRegister);
  els.pwHint.classList.toggle("hidden", !isRegister);
  els.pwSpacer.classList.toggle("hidden", isRegister);
  els.password.autocomplete = isRegister ? "new-password" : "current-password";
  els.submit.textContent = isRegister ? "Create account" : "Sign in";

  hide(els.error);
  hide(els.notice);
}

const show = (el, msg) => { el.textContent = msg; el.classList.remove("hidden"); };
const hide = (el) => el.classList.add("hidden");

function busy(on) {
  els.submit.disabled = on;
  els.submit.textContent = on
    ? (mode === "register" ? "Creating…" : "Signing in…")
    : (mode === "register" ? "Create account" : "Sign in");
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",   // let the browser store the httpOnly refresh cookie
  });
  let data = {};
  try { data = await res.json(); } catch { /* 204 etc. */ }
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.";
    throw new Error(detail);
  }
  return data;
}

async function onSubmit(e) {
  e.preventDefault();
  hide(els.error);
  hide(els.notice);

  const email = els.email.value.trim();
  const password = els.password.value;
  if (!email || !password) { show(els.error, "Please enter your email and password."); return; }

  busy(true);
  try {
    if (mode === "register") {
      const user = await post("/auth/register", {
        email, password, full_name: els.fullName.value.trim(),
      });
      show(els.notice, `Account created (role: ${user.role}). Signing you in…`);
      // Registration does not log you in — do it explicitly.
      const session = await post("/auth/login", { email, password });
      setSession(session);
      window.location.href = "/";
    } else {
      const session = await post("/auth/login", { email, password });
      setSession(session);
      window.location.href = "/";
    }
  } catch (err) {
    clearSession();
    show(els.error, err.message);
  } finally {
    busy(false);
  }
}

async function onForgot() {
  const email = els.email.value.trim();
  if (!email) { show(els.error, "Enter your email address first."); return; }
  hide(els.error);
  try {
    await post("/auth/password-reset/request", { email });
  } catch { /* the endpoint is intentionally uninformative */ }
  // Always the same message — revealing whether an account exists is an
  // enumeration oracle.
  show(els.notice, "If that account exists, a reset link has been sent.");
}

function init() {
  initTheme();
  setMode("login");
  els.tabLogin.addEventListener("click", () => setMode("login"));
  els.tabRegister.addEventListener("click", () => setMode("register"));
  els.form.addEventListener("submit", onSubmit);
  els.forgot.addEventListener("click", onForgot);
}

init();
