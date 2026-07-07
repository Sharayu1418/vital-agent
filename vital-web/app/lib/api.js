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
};
