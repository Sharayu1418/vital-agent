import test from "node:test";
import assert from "node:assert/strict";

process.env.NEXT_PUBLIC_API_BASE = "http://api.test";

const { bootstrapSession, resetSession, setTokenProvider } =
  await import("../app/lib/api.js");

const tick = (ms) => new Promise((r) => setTimeout(r, ms));

function stubFetch({ delay = 0, fail = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    if (delay) await tick(delay);
    if (fail) throw new Error("network down");
    return { ok: true, status: 200, json: async () => ({ ready: true }) };
  };
  return calls;
}

test.beforeEach(() => {
  resetSession();
  setTokenProvider(null);
});

// ---------- P1-9: one session, not three ----------

test("concurrent callers share ONE bootstrap request", async () => {
  // The bug: sleep/calendar/memories fired in parallel, each resolved
  // identity, each could mint a DIFFERENT anonymous session. Last cookie
  // won and data under the losers was orphaned.
  const calls = stubFetch({ delay: 10 });
  await Promise.all([bootstrapSession(), bootstrapSession(), bootstrapSession()]);
  assert.equal(calls.length, 1, "three callers must not mint three sessions");
  assert.equal(calls[0], "http://api.test/session");
});

test("later callers reuse the settled session without refetching", async () => {
  const calls = stubFetch();
  await bootstrapSession();
  await bootstrapSession();
  await bootstrapSession();
  assert.equal(calls.length, 1);
});

test("memoization is on the promise, not a flag set afterwards", async () => {
  // A boolean guard set AFTER the await would let both callers through and
  // recreate the race. Start the second while the first is still in flight.
  const calls = stubFetch({ delay: 20 });
  const first = bootstrapSession();
  const second = bootstrapSession();     // in-flight, must not fetch again
  await Promise.all([first, second]);
  assert.equal(calls.length, 1);
});

test("a failed bootstrap is not cached, so the app can recover", async () => {
  stubFetch({ fail: true });
  await assert.rejects(() => bootstrapSession());
  const calls = stubFetch();             // network comes back
  await bootstrapSession();
  assert.equal(calls.length, 1, "must retry after a failure, not stay broken");
});

test("resetSession forces the next identity to bootstrap its own session", async () => {
  const calls = stubFetch();
  await bootstrapSession();
  resetSession();                        // sign-out / account switch
  await bootstrapSession();
  assert.equal(calls.length, 2, "a new identity must not inherit the old session");
});

// ---------- P1-10: one dead endpoint must not blank the panel ----------

test("allSettled keeps healthy panel sections when a sibling fails", async () => {
  // mirrors refreshPanel(): Promise.all rejected the whole batch and the
  // catch blanked sleep, calendar AND memories together.
  const results = await Promise.allSettled([
    Promise.resolve({ ok: true, json: async () => ({ nights: [1, 2] }) }),
    Promise.reject(new Error("calendar 500")),
    Promise.resolve({ ok: true, json: async () => ({ memories: ["a"] }) }),
  ]);
  const ok = (r) => (r.status === "fulfilled" && r.value.ok ? r.value : null);
  assert.ok(ok(results[0]), "sleep survives");
  assert.equal(ok(results[1]), null, "calendar is the only casualty");
  assert.ok(ok(results[2]), "memories survive");
});

test("a non-ok response is dropped without taking siblings with it", async () => {
  const results = await Promise.allSettled([
    Promise.resolve({ ok: false, status: 500 }),
    Promise.resolve({ ok: true, json: async () => ({ memories: [] }) }),
  ]);
  const ok = (r) => (r.status === "fulfilled" && r.value.ok ? r.value : null);
  assert.equal(ok(results[0]), null);
  assert.ok(ok(results[1]));
});
