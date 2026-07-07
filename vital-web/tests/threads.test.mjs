import test from "node:test";
import assert from "node:assert/strict";

import {
  loadThreads, newThread, renameIfNew, saveThreads, titleFrom,
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
