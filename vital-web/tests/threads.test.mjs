import test from "node:test";
import assert from "node:assert/strict";

import {
  loadThreads, mergeThreads, newThread, renameIfNew, saveThreads, titleFrom,
} from "../app/lib/threads.js";

function fakeStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem: (k) => store[k] ?? null,
    setItem: (k, v) => { store[k] = v; },
    _store: store,
  };
}

test("titleFrom truncates at word boundary with ellipsis", () => {
  assert.equal(titleFrom("short message"), "short message");
  const long = titleFrom("I slept terribly all week and I want to understand why exactly");
  assert.ok(long.length <= 43 && long.endsWith("…"));
  assert.ok(!long.includes("  "));
});

test("thread ids are unique and API-pattern-safe", () => {
  const a = newThread(), b = newThread();
  assert.notEqual(a.id, b.id);
  assert.match(a.id, /^[\w-]+$/); // must satisfy backend thread_id pattern
});

test("save/load round-trip", () => {
  const s = fakeStorage();
  const t = newThread();
  saveThreads([t], s);
  assert.deepEqual(loadThreads(s), [t]);
});

test("load tolerates garbage storage", () => {
  assert.deepEqual(loadThreads(fakeStorage({ vital_threads: "{not json" })), []);
  assert.deepEqual(loadThreads(fakeStorage({ vital_threads: '{"a":1}' })), []);
});

test("renameIfNew titles only untitled threads", () => {
  const t = { ...newThread(), title: "New chat" };
  const named = { ...newThread(), title: "existing" };
  const out = renameIfNew([t, named], t.id, "plan my weekend please");
  assert.equal(out[0].title, "plan my weekend please");
  assert.equal(out[1].title, "existing");
  const out2 = renameIfNew(out, t.id, "second message");
  assert.equal(out2[0].title, "plan my weekend please"); // renames only once
});

test("saveThreads caps the list at 50", () => {
  const s = fakeStorage();
  saveThreads(Array.from({ length: 80 }, () => newThread()), s);
  assert.equal(loadThreads(s).length, 50);
});

// ---- signed-in cross-device merge ----

const serverRow = (id, title, at) => ({
  thread_id: id, title, created_at: at, updated_at: at,
});

test("server thread metadata merges over the local list", () => {
  const local = [
    { id: "t1", title: "Local title", createdAt: 1000 },
    { id: "t2", title: "Local only", createdAt: 2000 },
  ];
  const merged = mergeThreads(local, [
    serverRow("t1", "Server title", "2026-07-09T10:00:00Z"),
    serverRow("t3", "Other device", "2026-07-10T10:00:00Z"),
  ]);
  const byId = Object.fromEntries(merged.map((t) => [t.id, t]));
  assert.equal(byId.t1.title, "Server title");   // server wins on conflicts
  assert.equal(byId.t2.title, "Local only");     // local-only kept
  assert.equal(byId.t3.title, "Other device");   // cross-device row appears
  assert.equal(merged.length, 3);                // no duplicates
});

test("merge sorts newest activity first and caps the list", () => {
  const many = Array.from({ length: 60 }, (_, i) =>
    serverRow(`s${i}`, `S${i}`, new Date(2026, 0, i + 1).toISOString()));
  const merged = mergeThreads([], many);
  assert.equal(merged.length, 50);
  assert.equal(merged[0].id, "s59");             // most recent first
});

test("merge only ever contains rows the caller supplied", () => {
  // the backend scopes /threads to the resolved identity; the client-side
  // merge must never invent or import ids from anywhere else
  const merged = mergeThreads([{ id: "mine", title: "Mine", createdAt: 1 }],
                              [serverRow("from-server", "OK", "2026-07-01")]);
  assert.deepEqual(new Set(merged.map((t) => t.id)),
                   new Set(["mine", "from-server"]));
});
