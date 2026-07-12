/* Thin API client. Every call carries credentials (session cookie), and —
 * once the app registers a token provider — a Firebase ID token as
 * `Authorization: Bearer <token>`. Token logic lives HERE only:
 * components never touch headers.
 *
 * 401 handling: if a request that carried a token gets 401 (typically an
 * expired token racing its refresh), force-refresh once and retry once.
 * The `retried` flag makes an infinite loop impossible. */

const API = process.env.NEXT_PUBLIC_API_BASE;

const json = { "Content-Type": "application/json" };

/* (force) => Promise<string|null>. Registered by page.jsx after auth
 * boots; returns null when signed out, so anonymous flows send no header.
 * If it THROWS for a signed-in user, the request throws too — before any
 * fetch. Downgrading a token failure to an anonymous call would hit the
 * backend under a different identity, which is never what the user meant. */
let tokenProvider = null;

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
  const res = await fetch(`${API}${path}`, {
    credentials: "include", ...options, headers,
  });
  if (res.status === 401 && token) {
    if (!retried) return request(path, options, true);  // one retry, then done
    onUnauthorized?.();  // refreshed token still rejected: re-auth needed
  }
  return res;
}

export const api = {
  chat: (message, threadId) =>
    request("/chat", { method: "POST", headers: json,
      body: JSON.stringify({ message, thread_id: threadId }) }),

  approve: (action, feedback, threadId) =>
    request("/approve", { method: "POST", headers: json,
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
