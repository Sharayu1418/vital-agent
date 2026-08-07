/* Thin API client. Every call carries credentials (session cookie), and —
 * once the app registers a token provider — a Firebase ID token as
 * `Authorization: Bearer <token>`. Token logic lives HERE only:
 * components never touch headers.
 *
 * 401 handling: if a request that carried a token gets 401 (typically an
 * expired token racing its refresh), force-refresh once and retry once.
 * The `retried` flag makes an infinite loop impossible. */

import { readGeo } from "./theme";

const API = process.env.NEXT_PUBLIC_API_BASE;

const json = { "Content-Type": "application/json" };

/* (force) => Promise<string|null>. Registered by page.jsx after auth
 * boots; returns null when signed out, so anonymous flows send no header.
 * If it THROWS for a signed-in user, the request throws too — before any
 * fetch. Downgrading a token failure to an anonymous call would hit the
 * backend under a different identity, which is never what the user meant. */
let tokenProvider = null;

/* Reads the saved location straight from storage on every request.
 *
 * Deliberately not held in a module variable or React state: the location
 * picker, the device-geolocation prompt and a second browser tab can all
 * change it, and a cached copy would keep sending the old city until a
 * reload. Storage is the one place all three of them write to.
 *
 * readGeo is theme.js's — the same function the daylight theme uses. Two
 * readers of one key is how the panel and the agents disagreed in the
 * first place. */
function currentGeo() {
  if (typeof window === "undefined") return null;
  try {
    return readGeo(window.localStorage);
  } catch {
    return null;   // unreadable storage costs the city, not the request
  }
}

export function setTokenProvider(fn) {
  tokenProvider = fn;
}

/* Called when a SIGNED-IN request stays 401 even after a forced token
 * refresh — the session is genuinely dead and the user must sign in again.
 * page.jsx registers a handler that surfaces "Please sign in again." */
let onUnauthorized = null;

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

export async function request(path, options = {}, retried = false) {
  // token first; a provider error aborts the request before fetch runs.
  // On the retry pass this forces a refresh — if THAT fails, same rule:
  // propagate, no anonymous second attempt.
  const token = tokenProvider ? await tokenProvider(retried) : null;
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  /* The server runs in UTC and cannot know the user's day. Without this,
   * a sleep log at 23:00 Eastern is filed under tomorrow and the energy
   * forecast is anchored to the wrong clock. getTimezoneOffset() returns
   * minutes to SUBTRACT from local to reach UTC, so negate it. Read per
   * request, not once at load: laptops travel and DST happens. */
  headers["X-UTC-Offset"] = String(-new Date().getTimezoneOffset());
  /* Location travels with the request too. It used to live only in
   * localStorage, driving the theme and the side panel while never
   * reaching the server — so the panel could say "New York" while the
   * agent's tools queried Albany, which it had learned from an old
   * conversation. Read fresh each time so changing it takes effect on the
   * very next message with nothing to invalidate. */
  const geo = currentGeo();
  if (geo) {
    headers["X-Geo-Lat"] = String(geo.lat);
    headers["X-Geo-Lng"] = String(geo.lng);
    // Headers are latin-1; place names are not. Encode, and let the
    // server decode.
    if (geo.label) headers["X-Geo-Label"] = encodeURIComponent(geo.label);
  }
  const res = await fetch(`${API}${path}`, {
    credentials: "include", ...options, headers,
  });
  if (res.status === 401 && token) {
    if (!retried) return request(path, options, true);  // one retry, then done
    onUnauthorized?.();  // refreshed token still rejected: re-auth needed
  }
  return res;
}

/* P1-9: identity must exist before anything fans out.
 *
 * Every identity-resolving endpoint can mint a new anonymous session, so
 * three parallel calls could each mint a DIFFERENT one — last cookie wins,
 * and data written under the losers is orphaned. This resolves identity in
 * a single request first.
 *
 * Memoized on the PROMISE, not on a boolean: concurrent callers must await
 * the same in-flight request rather than each firing their own, which would
 * recreate the exact race. resetSession() clears it on sign-out/switch so
 * the next identity bootstraps cleanly. */
let sessionReady = null;

export function bootstrapSession() {
  if (!sessionReady) {
    // a failed bootstrap must not be cached, or the app never retries
    sessionReady = request("/session").catch((err) => {
      sessionReady = null;
      throw err;
    });
  }
  return sessionReady;
}

export function resetSession() {
  sessionReady = null;
}

export const api = {
  // `signal` lets the caller cancel a stream (stop button, thread switch).
  // Without it an abandoned turn kept streaming to completion in the
  // background, burning tokens for output nobody would ever see.
  chat: (message, threadId, signal) =>
    request("/chat", { method: "POST", headers: json, signal,
      body: JSON.stringify({ message, thread_id: threadId }) }),

  approve: (action, feedback, threadId, signal) =>
    request("/approve", { method: "POST", headers: json, signal,
      body: JSON.stringify({ action, feedback, thread_id: threadId }) }),

  upload: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/upload/health", { method: "POST", body: form });
  },

  feedback: (rating, threadId) =>
    request("/feedback", { method: "POST", headers: json,
      body: JSON.stringify({ rating, thread_id: threadId }) }).catch(() => {}),

  threads: () => request("/threads"),
  threadMessages: (threadId) => request(`/threads/${threadId}/messages`),
  // unlists from the caller's own sidebar index (server keeps conversation)
  threadDelete: (threadId) => request(`/threads/${threadId}`, { method: "DELETE" }),

  sleepRecent: () => request("/sleep/recent"),
  forecast: (hours = 24) => request(`/forecast?horizon_hours=${hours}`),

  // Returns a PDF, so the caller reads res.blob() rather than res.json().
  meetingPlan: (requestId) =>
    request(`/activity-requests/${requestId}/meeting.pdf`),

  // ---- morning brief ----
  briefSettings: () => request("/brief/settings"),
  saveBriefSettings: (payload) =>
    request("/brief/settings", { method: "POST", headers: json,
      body: JSON.stringify(payload) }),
  briefPreview: () => request("/brief/preview", { method: "POST" }),
  briefTest: () => request("/brief/test", { method: "POST" }),
  briefSubscribe: (subscription) =>
    request("/brief/subscribe", { method: "POST", headers: json,
      body: JSON.stringify(subscription) }),
  briefUnsubscribe: (endpoint) =>
    request(`/brief/subscribe?endpoint=${encodeURIComponent(endpoint)}`,
      { method: "DELETE" }),

  // ---- wearable connections ----
  connections: () => request("/connections"),
  connectStart: (provider) => request(`/connect/${provider}`),
  connectSync: (provider) => request(`/connect/${provider}/sync`, { method: "POST" }),
  connectDisconnect: (provider) =>
    request(`/connect/${provider}`, { method: "DELETE" }),
  calendar: () => request("/calendar"),
  memories: () => request("/memories"),
  forget: (key) => request(`/memories/${key}`, { method: "DELETE" }),

  logout: () => request("/auth/logout", { method: "POST" }).catch(() => {}),

  // ---- Activity Buddy Board ----
  buddyCreate: (payload) =>
    request("/activity-posts", { method: "POST", headers: json,
      body: JSON.stringify(payload) }),
  buddySearch: (queryString) =>
    request(`/activity-posts${queryString ? `?${queryString}` : ""}`),
  buddyMine: () => request("/activity-posts/mine"),
  buddyUpdate: (postId, patch) =>
    request(`/activity-posts/${postId}`, { method: "PATCH", headers: json,
      body: JSON.stringify(patch) }),
  buddyRequestJoin: (postId, message, requesterName) =>
    request(`/activity-posts/${postId}/request`, { method: "POST", headers: json,
      body: JSON.stringify({ message, requester_name: requesterName }) }),
  buddyRequests: () => request("/activity-requests/mine"),
  buddyDecide: (requestId, status) =>
    request(`/activity-requests/${requestId}`, { method: "PATCH", headers: json,
      body: JSON.stringify({ status }) }),
  buddyReport: (postId, reason) =>
    request(`/activity-posts/${postId}/report`, { method: "POST", headers: json,
      body: JSON.stringify({ reason }) }),
  buddyBlock: (ownerKey) =>
    request(`/users/${ownerKey}/block`, { method: "POST", headers: json,
      body: JSON.stringify({}) }),
};
