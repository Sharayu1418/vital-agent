/* Thread-list persistence (localStorage) — pure functions, node-testable.
 * Messages themselves live server-side in the checkpointer and are fetched
 * via GET /threads/:id/messages; only the list + titles live client-side. */

const KEY = "vital_threads";

/* crypto.randomUUID only exists in secure contexts (https / localhost) —
 * accessing the dev server via LAN IP (http://192.168.x.x) loses it.
 * Message/thread ids don't need cryptographic strength, just uniqueness. */
export function uid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

export function titleFrom(text) {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length <= 42) return clean;
  const cut = clean.slice(0, 42);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > 24 ? cut.slice(0, lastSpace) : cut) + "…";
}

export function newThread() {
  return {
    id: `t${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`,
    title: "New chat",
    createdAt: Date.now(),
  };
}

export function loadThreads(storage) {
  try {
    const raw = storage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) && list.every((t) => t.id && t.title) ? list : [];
  } catch {
    return [];
  }
}

export function saveThreads(list, storage) {
  try {
    storage.setItem(KEY, JSON.stringify(list.slice(0, 50)));
  } catch {
    /* quota/private mode: threads just don't persist */
  }
}

export function renameIfNew(list, id, firstMessage) {
  return list.map((t) =>
    t.id === id && t.title === "New chat" ? { ...t, title: titleFrom(firstMessage) } : t);
}

/* Merge the server thread index (signed-in, cross-device) with the local
 * list. Server rows win on title for the same id; local-only threads are
 * kept (they may predate sign-in on this device). Sorted newest-activity
 * first. Server rows are the caller's own by construction — the backend
 * scopes them to the resolved identity. */
export function mergeThreads(local, server) {
  const merged = new Map();
  for (const t of local) merged.set(t.id, { ...t });
  for (const s of server) {
    const at = Date.parse(s.updated_at || s.created_at) || Date.now();
    merged.set(s.thread_id, {
      id: s.thread_id,
      title: s.title || "New chat",
      createdAt: Date.parse(s.created_at) || at,
      activeAt: at,
    });
  }
  return [...merged.values()]
    .sort((a, b) => (b.activeAt || b.createdAt || 0) - (a.activeAt || a.createdAt || 0))
    .slice(0, 50);
}
