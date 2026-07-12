import test from "node:test";
import assert from "node:assert/strict";

process.env.NEXT_PUBLIC_API_BASE = "http://api.test";

const { gateFor, anonAllowed } = await import("../app/lib/auth.js");
const { request, setTokenProvider, setUnauthorizedHandler } =
  await import("../app/lib/api.js");

// ---------- the OAuth-first gate decision ----------

test("auth loading never resolves to the app shell", () => {
  // ready:false → neither app nor login, regardless of everything else
  assert.equal(gateFor({ ready: false, user: null, configured: true }), "loading");
  assert.equal(gateFor({ ready: false, user: { uid: "x" }, configured: true }),
               "loading");
});

test("configured + signed out → login screen only", () => {
  assert.equal(gateFor({ ready: true, user: null, configured: true }), "login");
});

test("signed in → app shell", () => {
  assert.equal(gateFor({ ready: true, user: { uid: "x" }, configured: true }),
               "app");
});

test("unconfigured Firebase → helpful message, unless anon dev is allowed", () => {
  assert.equal(gateFor({ ready: true, user: null, configured: false,
                         allowAnon: false }), "unconfigured");
  assert.equal(gateFor({ ready: true, user: null, configured: false,
                         allowAnon: true }), "app");
});

test("only the 'app' gate ever loads data (mirrors page.jsx effect guard)", () => {
  // the data-loading effect runs iff gate === "app"; prove no other state
  // (loading / login / unconfigured-without-anon) can reach it
  for (const g of [
    gateFor({ ready: false, user: null, configured: true }),
    gateFor({ ready: true, user: null, configured: true }),
    gateFor({ ready: true, user: null, configured: false, allowAnon: false }),
  ]) {
    assert.notEqual(g, "app");
  }
});

test("anonAllowed reads the explicit env flag only", () => {
  delete process.env.NEXT_PUBLIC_ALLOW_ANON;
  assert.equal(anonAllowed(), false);
  process.env.NEXT_PUBLIC_ALLOW_ANON = "1";
  assert.equal(anonAllowed(), true);
  process.env.NEXT_PUBLIC_ALLOW_ANON = "yes";
  assert.equal(anonAllowed(), false);   // strictly "1"
  delete process.env.NEXT_PUBLIC_ALLOW_ANON;
});

// ---------- "Please sign in again." on a persistent 401 ----------

function stubFetch(responses) {
  const calls = [];
  globalThis.fetch = async () => {
    const status = responses[Math.min(calls.length, responses.length - 1)];
    calls.push(status);
    return { status, ok: status < 400 };
  };
  return calls;
}

test("persistent 401 for a signed-in user fires the unauthorized handler once", async () => {
  const calls = stubFetch([401, 401]);
  let fired = 0;
  setTokenProvider(async () => "tok");
  setUnauthorizedHandler(() => { fired += 1; });
  const res = await request("/memories");
  assert.equal(res.status, 401);
  assert.equal(calls.length, 2);   // original + one refresh retry
  assert.equal(fired, 1);          // then surface "Please sign in again."
  setTokenProvider(null);
  setUnauthorizedHandler(null);
});

test("anonymous 401 does NOT fire the re-auth handler", async () => {
  stubFetch([401]);
  let fired = 0;
  setTokenProvider(async () => null);   // signed out
  setUnauthorizedHandler(() => { fired += 1; });
  await request("/memories");
  assert.equal(fired, 0);
  setTokenProvider(null);
  setUnauthorizedHandler(null);
});
