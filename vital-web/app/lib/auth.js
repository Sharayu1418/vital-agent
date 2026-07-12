/* Google Sign-In via Firebase Auth.
 *
 * Design: page.jsx subscribes once with watchAuth (wraps onIdTokenChanged,
 * so refreshed tokens flow through the same path); api.js pulls tokens via
 * idToken(). Nothing here stores tokens itself — the Firebase SDK owns
 * persistence and refresh. */

import { getFirebaseAuth } from "./firebase.js";

/* Subscribe to auth state. onChange(user|null) fires once the INITIAL
 * state is known (that first call is the "auth ready" signal) and again on
 * every sign-in/out and token refresh. Returns an unsubscribe function.
 * With Firebase unconfigured (or during SSR) it reports null immediately —
 * the app just stays anonymous. */
export async function watchAuth(onChange) {
  const auth = await getFirebaseAuth();
  if (!auth) {
    onChange(null);
    return () => {};
  }
  const { onIdTokenChanged } = await import("firebase/auth");
  return onIdTokenChanged(auth, (user) => onChange(user ?? null));
}

/* Current user's ID token. Returns null ONLY when signed out or
 * unconfigured — that's the one case where anonymous is correct. A
 * getIdToken() failure for a signed-in user PROPAGATES: swallowing it
 * would silently downgrade an account request to a fresh anonymous
 * identity and split the user's data. force=true asks Firebase for a
 * fresh token (used once after a 401). */
export async function idToken(force = false) {
  const auth = await getFirebaseAuth();
  const user = auth?.currentUser;
  if (!user) return null;
  return user.getIdToken(force);
}

/* Popup first (best desktop UX); redirect when the environment can't do
 * popups. Throws with a short human message on real failures. */
export async function signInWithGoogle() {
  const auth = await getFirebaseAuth();
  if (!auth) throw new Error("Sign-in isn't configured on this deployment.");
  const { GoogleAuthProvider, signInWithPopup, signInWithRedirect } =
    await import("firebase/auth");
  const provider = new GoogleAuthProvider();
  try {
    await signInWithPopup(auth, provider);
  } catch (err) {
    if (REDIRECT_FALLBACK_CODES.has(err?.code)) {
      await signInWithRedirect(auth, provider);  // resolves after page return
      return;
    }
    throw new Error(authErrorText(err?.code));
  }
}

export async function signOutUser() {
  const auth = await getFirebaseAuth();
  if (!auth) return;
  const { signOut } = await import("firebase/auth");
  await signOut(auth);
}

/* ---------- pure helpers (node-testable) ---------- */

/* Local-dev escape hatch: with Firebase unconfigured, the app may run
 * anonymously ONLY when this flag is set (never in production, where the
 * Firebase env vars exist and the gate demands sign-in). */
export function anonAllowed() {
  return process.env.NEXT_PUBLIC_ALLOW_ANON === "1";
}

/* The OAuth-first gate, as a pure decision so it's testable:
 * "loading"      → auth state unknown: render neither app nor login
 * "login"        → configured, signed out: login screen only
 * "unconfigured" → no Firebase config and no anon escape: helpful message
 * "app"          → signed in (or explicitly-allowed anonymous local dev)
 * Data loading and user-data API calls are allowed ONLY in "app". */
export function gateFor({ ready, user, configured, allowAnon }) {
  if (!ready) return "loading";
  if (user) return "app";
  if (!configured) return allowAnon ? "app" : "unconfigured";
  return "login";
}

export const REDIRECT_FALLBACK_CODES = new Set([
  "auth/popup-blocked",
  "auth/operation-not-supported-in-this-environment",
]);

export function authErrorText(code) {
  switch (code) {
    case "auth/popup-closed-by-user":
    case "auth/cancelled-popup-request":
    case "auth/user-cancelled":
      return "Sign-in was cancelled.";
    case "auth/network-request-failed":
      return "Network hiccup. Check your connection and try again.";
    case "auth/unauthorized-domain":
      return "This site isn't authorized for sign-in yet.";
    case "auth/too-many-requests":
      return "Too many attempts. Wait a minute and try again.";
    default:
      return "Sign-in didn't work. Please try again.";
  }
}

/* Short, safe label for the sidebar. Provider values are TEXT (React
 * escapes them), but we still strip control/markup characters and cap the
 * length so layouts and logs stay sane. */
export function accountLabel(user) {
  const raw = (user?.displayName || user?.email || "").trim();
  const clean = raw.replace(/[<>"'`\u0000-\u001f]/g, "").trim();
  return clean.length > 28 ? `${clean.slice(0, 27)}…` : clean || "Signed in";
}

/* Google first name, suggested ONLY when no VITAL name was chosen —
 * the caller checks that. Reuses the same validation as manual entry. */
export function suggestedFirstName(user, firstNameFrom) {
  return user?.displayName ? firstNameFrom(user.displayName) : "";
}

/* Sign-out hygiene: remove anything in web storage that references the
 * session transport or account. The HttpOnly cookie is expired server-side
 * via POST /auth/logout — and even a surviving cookie is useless, because
 * the backend rejects anonymous sessions linked to an account. */
export function clearSessionTransport(storage) {
  for (const key of ["vital_session", "x_vital_session", "vital_session_header"]) {
    try {
      storage.removeItem(key);
    } catch { /* private mode */ }
  }
}
