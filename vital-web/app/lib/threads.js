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
