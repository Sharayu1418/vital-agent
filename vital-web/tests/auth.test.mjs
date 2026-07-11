import test from "node:test";
import assert from "node:assert/strict";

process.env.NEXT_PUBLIC_API_BASE = "http://api.test";

const { firebaseConfigured, getFirebaseAuth } = await import("../app/lib/firebase.js");
const {
  accountLabel, authErrorText, clearSessionTransport, suggestedFirstName,
  watchAuth,
} = await import("../app/lib/auth.js");
const { api, request, setTokenProvider } = await import("../app/lib/api.js");
const { firstNameFrom } = await import("../app/lib/theme.js");

// ---------- SSR safety + auth-ready gating ----------

test("firebase is inert without window / config (SSR-safe)", async () => {
  assert.equal(typeof window, "undefined");     // node = the SSR condition
  assert.equal(firebaseConfigured(), false);    // no NEXT_PUBLIC_FIREBASE_*
  assert.equal(await getFirebaseAuth(), null);  // no throw, no SDK import
});

test("watchAuth reports signed-out immediately when unconfigured", async () => {
  const seen = [];
  const unsubscribe = await watchAuth((user) => seen.push(user));
  assert.deepEqual(seen, [null]);               // the auth-ready signal
  assert.equal(typeof unsubscribe, "function");
  unsubscribe();
});

// ---------- bearer header injection + 401 retry ----------

function stubFetch(responses) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    const status = responses[Math.min(calls.length - 1, responses.length - 1)];
    return { status, ok: status < 400 };
  };
  return calls;
}

test("signed in: every request carries the Firebase bearer token", async () => {
  const calls = stubFetch([200]);
  setTokenProvider(async () => "tok-123");
  const res = await request("/memories");
  assert.equal(res.ok, true);
  assert.equal(calls[0].options.headers.Authorization, "Bearer tok-123");
  assert.equal(calls[0].options.credentials, "include"); // cookie still rides
  setTokenProvider(null);
});

test("signed out: no Authorization header at all", async () => {
  const calls = stubFetch([200]);
  setTokenProvider(async () => null);           // firebase user absent
  await request("/memories");
  assert.equal("Authorization" in calls[0].options.headers, false);
  setTokenProvider(null);
});

test("401 with a token: force-refresh once, retry once, succeed", async () => {
  const calls = stubFetch([401, 200]);
  const forces = [];
  setTokenProvider(async (force) => { forces.push(force); return "tok"; });
  const res = await request("/chat", { method: "POST" });
  assert.equal(res.status, 200);
  assert.equal(calls.length, 2);
  assert.deepEqual(forces, [false, true]);      // second pass forces refresh
  setTokenProvider(null);
});

test("persistent 401 never loops: exactly two attempts", async () => {
  const calls = stubFetch([401, 401, 401]);
  setTokenProvider(async () => "tok");
  const res = await request("/chat", { method: "POST" });
  assert.equal(res.status, 401);
  assert.equal(calls.length, 2);
  setTokenProvider(null);
});

test("anonymous 401 is returned as-is (no pointless retry)", async () => {
  const calls = stubFetch([401]);
  setTokenProvider(async () => null);
  const res = await api.memories();
  assert.equal(res.status, 401);
  assert.equal(calls.length, 1);
  setTokenProvider(null);
});

test("token retrieval failure: fetch is NEVER called, error propagates", async () => {
  // a signed-in user whose getIdToken() fails must not be silently
  // downgraded to an anonymous request under a different identity
  const calls = stubFetch([200]);
  setTokenProvider(async () => { throw new Error("token backend down"); });
  await assert.rejects(() => request("/memories"), /token backend down/);
  assert.equal(calls.length, 0);
  setTokenProvider(null);
});

test("threadDelete targets the caller's thread with auth attached", async () => {
  const calls = stubFetch([200]);
  setTokenProvider(async () => "tok-abc");
  const res = await api.threadDelete("t-old");
  assert.equal(res.ok, true);
  assert.ok(calls[0].url.endsWith("/threads/t-old"));
  assert.equal(calls[0].options.method, "DELETE");
  assert.equal(calls[0].options.headers.Authorization, "Bearer tok-abc");
  setTokenProvider(null);
});

test("refresh failure after 401: no anonymous retry, error propagates", async () => {
  const calls = stubFetch([401, 200]);
  setTokenProvider(async (force) => {
    if (force) throw new Error("refresh failed");
    return "tok-stale";
  });
  await assert.rejects(() => request("/chat", { method: "POST" }), /refresh failed/);
  assert.equal(calls.length, 1);   // only the original 401 attempt hit the wire
  setTokenProvider(null);
});

// ---------- sign-out hygiene ----------

test("clearSessionTransport removes session keys and survives errors", () => {
  const store = new Map([
    ["vital_session", "abc"], ["x_vital_session", "def"],
    ["vital_threads", "keep-until-page-clears-it"],
  ]);
  clearSessionTransport({ removeItem: (k) => store.delete(k) });
  assert.equal(store.has("vital_session"), false);
  assert.equal(store.has("x_vital_session"), false);
  clearSessionTransport({ removeItem: () => { throw new Error("private mode"); } });
});

// ---------- display safety ----------

test("accountLabel sanitizes provider values and caps length", () => {
  assert.equal(accountLabel({ displayName: "Sharayu Rasal" }), "Sharayu Rasal");
  assert.equal(accountLabel({ email: "srr10019@nyu.edu" }), "srr10019@nyu.edu");
  assert.equal(accountLabel({ displayName: '<img src=x> "Sam"' }), "img src=x Sam");
  assert.equal(accountLabel({}), "Signed in");
  assert.ok(accountLabel({ displayName: "x".repeat(60) }).length <= 28);
});

test("authErrorText keeps messages short and human", () => {
  assert.match(authErrorText("auth/popup-closed-by-user"), /cancelled/i);
  assert.match(authErrorText("auth/network-request-failed"), /connection/i);
  assert.match(authErrorText("auth/unauthorized-domain"), /authorized/i);
  assert.match(authErrorText("auth/whatever-else"), /try again/i);
  assert.ok(authErrorText(undefined).length < 60);
});

test("google name is only a suggestion, validated like manual entry", () => {
  assert.equal(suggestedFirstName({ displayName: "sharayu rasal" }, firstNameFrom),
               "Sharayu");
  assert.equal(suggestedFirstName({}, firstNameFrom), "");
  assert.equal(suggestedFirstName({ displayName: "123" }, firstNameFrom), "");
});
