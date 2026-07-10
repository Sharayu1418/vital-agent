/* Thin API client — every call carries credentials (session cookie). */

const API = process.env.NEXT_PUBLIC_API_BASE;

const json = { "Content-Type": "application/json" };

export const api = {
  chat: (message, threadId) =>
    fetch(`${API}/chat`, { method: "POST", credentials: "include", headers: json,
      body: JSON.stringify({ message, thread_id: threadId }) }),

  approve: (action, feedback, threadId) =>
    fetch(`${API}/approve`, { method: "POST", credentials: "include", headers: json,
      body: JSON.stringify({ action, feedback, thread_id: threadId }) }),

  upload: (file) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${API}/upload/health`, { method: "POST", credentials: "include", body: form });
  },

  feedback: (rating, threadId) =>
    fetch(`${API}/feedback`, { method: "POST", credentials: "include", headers: json,
      body: JSON.stringify({ rating, thread_id: threadId }) }).catch(() => {}),

  threadMessages: (threadId) =>
    fetch(`${API}/threads/${threadId}/messages`, { credentials: "include" }),

  sleepRecent: () => fetch(`${API}/sleep/recent`, { credentials: "include" }),
  calendar: () => fetch(`${API}/calendar`, { credentials: "include" }),
  memories: () => fetch(`${API}/memories`, { credentials: "include" }),
  forget: (key) =>
    fetch(`${API}/memories/${key}`, { method: "DELETE", credentials: "include" }),

  // ---- Activity Buddy Board ----
  buddyCreate: (payload) =>
    fetch(`${API}/activity-posts`, { method: "POST", credentials: "include",
      headers: json, body: JSON.stringify(payload) }),
  buddySearch: (queryString) =>
    fetch(`${API}/activity-posts${queryString ? `?${queryString}` : ""}`,
      { credentials: "include" }),
  buddyMine: () => fetch(`${API}/activity-posts/mine`, { credentials: "include" }),
  buddyUpdate: (postId, patch) =>
    fetch(`${API}/activity-posts/${postId}`, { method: "PATCH", credentials: "include",
      headers: json, body: JSON.stringify(patch) }),
  buddyRequestJoin: (postId, message, requesterName) =>
    fetch(`${API}/activity-posts/${postId}/request`, { method: "POST",
      credentials: "include", headers: json,
      body: JSON.stringify({ message, requester_name: requesterName }) }),
  buddyRequests: () =>
    fetch(`${API}/activity-requests/mine`, { credentials: "include" }),
  buddyDecide: (requestId, status) =>
    fetch(`${API}/activity-requests/${requestId}`, { method: "PATCH",
      credentials: "include", headers: json, body: JSON.stringify({ status }) }),
  buddyReport: (postId, reason) =>
    fetch(`${API}/activity-posts/${postId}/report`, { method: "POST",
      credentials: "include", headers: json, body: JSON.stringify({ reason }) }),
  buddyBlock: (ownerKey) =>
    fetch(`${API}/users/${ownerKey}/block`, { method: "POST",
      credentials: "include", headers: json, body: JSON.stringify({}) }),
};
