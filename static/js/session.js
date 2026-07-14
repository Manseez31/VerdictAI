// session.js — client-side session handling.
//
// SECURITY MODEL
// --------------
// * The ACCESS token (short-lived, 15 min) is kept in sessionStorage. It is
//   readable by JS by necessity — it must go into the Authorization header.
//   Its blast radius is bounded by its short lifetime.
// * The REFRESH token (long-lived) is NEVER touched by JS. It lives in an
//   httpOnly, SameSite=Strict cookie set by the server, so XSS cannot read or
//   exfiltrate it and CSRF cannot send it cross-site. This split is the reason
//   stealing an access token is survivable and stealing a refresh token is not.
// * sessionStorage (not localStorage) means the token dies with the tab.

const KEY = "verdictai/session";

/** @returns {{access_token, role, permissions, expires_at}|null} */
export function getSession() {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (s.expires_at && Date.now() > s.expires_at) return null;   // expired
    return s;
  } catch {
    return null;
  }
}

export function setSession(tokenResponse) {
  const session = {
    access_token: tokenResponse.access_token,
    role: tokenResponse.role,
    permissions: tokenResponse.permissions || [],
    // Refresh a little early so a request never races the expiry.
    expires_at: Date.now() + Math.max(0, (tokenResponse.expires_in || 900) - 30) * 1000,
  };
  sessionStorage.setItem(KEY, JSON.stringify(session));
  return session;
}

export function clearSession() {
  sessionStorage.removeItem(KEY);
}

export function hasPermission(permission) {
  const s = getSession();
  return !!s && s.permissions.includes(permission);
}

/**
 * Silently rotate the access token using the httpOnly refresh cookie.
 * Returns the new session, or null if the session is gone (user must log in).
 */
export async function refreshSession() {
  try {
    const res = await fetch("/auth/refresh", { method: "POST", credentials: "same-origin" });
    if (!res.ok) {
      clearSession();
      return null;
    }
    return setSession(await res.json());
  } catch {
    clearSession();
    return null;
  }
}

/**
 * fetch() wrapper that attaches the access token and transparently refreshes it
 * once on a 401. Every authenticated page should use this instead of raw fetch.
 */
export async function authFetch(input, init = {}) {
  let session = getSession();
  if (!session) session = await refreshSession();   // token expired -> try to rotate

  const withAuth = (s) => ({
    ...init,
    credentials: "same-origin",
    headers: {
      ...(init.headers || {}),
      ...(s ? { Authorization: `Bearer ${s.access_token}` } : {}),
    },
  });

  let res = await fetch(input, withAuth(session));

  if (res.status === 401) {
    // The token may have been revoked (role change, disable, logout elsewhere).
    const rotated = await refreshSession();
    if (!rotated) {
      redirectToLogin();
      return res;
    }
    res = await fetch(input, withAuth(rotated));
  }
  return res;
}

export async function logout() {
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch { /* logout is best-effort; the local session is cleared regardless */ }
  clearSession();
  redirectToLogin();
}

export function redirectToLogin() {
  const next = encodeURIComponent(window.location.pathname);
  window.location.href = `/login?next=${next}`;
}

/**
 * Route guard for a page. Only enforces when the server says auth is required,
 * so the open (AUTH_REQUIRED=false) deployment keeps working with no login.
 */
export async function guardPage() {
  let required = false;
  try {
    const res = await fetch("/health");
    const body = await res.json();
    required = !!(body.security && body.security.auth_required);
  } catch {
    return;   // server unreachable — let the page render its own error state
  }
  if (!required) return;

  if (!getSession() && !(await refreshSession())) redirectToLogin();
}
