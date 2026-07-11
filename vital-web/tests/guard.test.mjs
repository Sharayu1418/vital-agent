import test from "node:test";
import assert from "node:assert/strict";

import { createGenerationGuard } from "../app/lib/guard.js";

const tick = (ms) => new Promise((r) => setTimeout(r, ms));

test("begin() starts a new epoch and kills older watchers", () => {
  const guard = createGenerationGuard();
  const first = guard.begin();
  assert.equal(first(), true);
  const second = guard.begin();
  assert.equal(first(), false);   // superseded
  assert.equal(second(), true);
});

test("watch() observes the current epoch without ending it", () => {
  const guard = createGenerationGuard();
  const epoch = guard.begin();
  const observer = guard.watch();
  assert.equal(epoch(), true);    // watch() must not invalidate begin()
  assert.equal(observer(), true);
  guard.invalidate();
  assert.equal(epoch(), false);
  assert.equal(observer(), false);
});

test("stale account response cannot write after sign-out", async () => {
  // simulates: account-A panel load in flight → sign-out → anon load
  const guard = createGenerationGuard();
  const writes = [];
  const load = async (label, delayMs, live) => {
    await tick(delayMs);
    if (live()) writes.push(label);   // the pattern page.jsx uses
  };

  const slowAccountLoad = load("account-A-panel", 25, guard.begin());
  await tick(5);
  guard.invalidate();                 // signOut(): synchronous, immediate
  const anonLoad = load("anon-panel", 5, guard.begin());
  await Promise.all([slowAccountLoad, anonLoad]);
  assert.deepEqual(writes, ["anon-panel"]);   // A's response was suppressed
});

test("switching accounts suppresses the previous account's responses", async () => {
  const guard = createGenerationGuard();
  const writes = [];
  const load = async (label, delayMs, live) => {
    await tick(delayMs);
    if (live()) writes.push(label);
  };
  const a = load("account-A", 20, guard.begin());   // uid change re-begins
  const b = load("account-B", 30, guard.begin());
  await Promise.all([a, b]);
  assert.deepEqual(writes, ["account-B"]);
});

test("stale account-A completion cannot flip busy/thinking during anon work", async () => {
  // mirrors send()/decide(): finally runs only when live(); signOut resets
  // the flags synchronously and starts a new epoch
  const guard = createGenerationGuard();
  const ui = { busy: false, thinking: false, panel: "account-A-data" };
  const sendLike = async (label, delayMs, live) => {
    ui.busy = true;
    ui.thinking = true;
    await tick(delayMs);
    if (live()) {                       // the guarded finally block
      ui.busy = false;
      ui.thinking = false;
      ui.panel = `${label}-panel`;
    }
  };

  const staleA = sendLike("account-A", 25, guard.watch());
  await tick(5);
  guard.invalidate();                   // signOut(): synchronous...
  ui.busy = false;                      // ...flag reset included
  ui.thinking = false;
  ui.panel = "cleared";
  const anon = sendLike("anon", 40, guard.begin());
  await tick(30);                       // A's response lands here, mid-anon-request
  assert.equal(ui.busy, true, "stale finally must not clear anon's busy flag");
  assert.equal(ui.thinking, true);
  assert.equal(ui.panel, "cleared", "stale completion must not write panel state");
  await Promise.all([staleA, anon]);
  assert.equal(ui.busy, false);         // anon's OWN completion clears it
  assert.equal(ui.panel, "anon-panel");
});

test("panel bodies parsed after sign-out are discarded", async () => {
  // mirrors refreshPanel(): parse first, final live() check, then write
  const guard = createGenerationGuard();
  let panel = null;
  const refreshLike = async (live) => {
    const body = await (async () => { await tick(20); return "account-A-nights"; })();
    if (!live()) return;                // check AFTER parsing completes
    panel = body;
  };
  const inFlight = refreshLike(guard.watch());
  await tick(5);
  guard.invalidate();                   // sign-out while json() still parsing
  await inFlight;
  assert.equal(panel, null);
});

test("stream consumption breaks at the first stale event", async () => {
  // mirrors consume(): the SSE loop stops pulling, not just writing
  const guard = createGenerationGuard();
  const live = guard.watch();
  const consumed = [];
  async function* stream() {
    for (let i = 0; i < 5; i += 1) { await tick(2); yield i; }
  }
  for await (const ev of stream()) {
    if (!live()) break;
    consumed.push(ev);
    if (ev === 1) guard.invalidate();   // sign-out mid-stream
  }
  assert.deepEqual(consumed, [0, 1]);   // events 2-4 never consumed
});
