import test from "node:test";
import assert from "node:assert/strict";

import {
  REQUIRED_FIELDS, SAFE_CARD_FIELDS, buildPostPayload, safeCard, searchQuery,
} from "../app/lib/buddies.js";

// ---- payload shaping ----

test("payload trims fields, drops empty optionals, keeps active flag", () => {
  const { payload, ok } = buildPostPayload({
    display_name: "  Swim Sam ", activity: "swimming", city: "Albany",
    area: "", notes: "  lap swims  ", vibe: "casual", active: true,
  });
  assert.equal(ok, true);
  assert.deepEqual(payload, {
    display_name: "Swim Sam", activity: "swimming", city: "Albany",
    notes: "lap swims", vibe: "casual", active: true,
  });
  assert.ok(!("area" in payload));           // empty optional not sent
});

test("missing required fields are reported, not sent", () => {
  const { ok, missing } = buildPostPayload({ activity: "swimming" });
  assert.equal(ok, false);
  assert.deepEqual(missing.sort(), ["city", "display_name"]);
});

test("active=false survives payload shaping (pause toggle)", () => {
  const { payload } = buildPostPayload({
    display_name: "S", activity: "swim", city: "Albany", active: false,
  });
  assert.equal(payload.active, false);
});

test("payload never invents a user_id — identity is server-side", () => {
  const { payload } = buildPostPayload({
    display_name: "S", activity: "swim", city: "Albany",
    user_id: "sneaky", id: 42,
  });
  assert.ok(!("user_id" in payload) && !("id" in payload));
});

test("nullish form is safe", () => {
  const { ok, missing } = buildPostPayload(undefined);
  assert.equal(ok, false);
  assert.deepEqual(missing, REQUIRED_FIELDS);
});

// ---- match cards render safe fields only ----

test("safeCard strips anything outside the whitelist", () => {
  const card = safeCard({
    id: 7, display_name: "Swim Sam", activity: "swimming", city: "Albany",
    match_score: 5, match_reasons: ["same activity: swimming"],
    user_id: "anon-deadbeef", email: "x@y.z", exact_address: "12 Main St",
  });
  assert.deepEqual(Object.keys(card).sort(), [
    "activity", "city", "display_name", "id", "match_reasons", "match_score",
  ]);
  assert.ok(!JSON.stringify(card).includes("anon-"));
  assert.ok(!JSON.stringify(card).includes("Main St"));
});

test("safeCard whitelist itself contains no private fields", () => {
  for (const banned of ["user_id", "email", "phone", "address"]) {
    assert.ok(!SAFE_CARD_FIELDS.includes(banned), `${banned} must not be renderable`);
  }
});

test("safeCard tolerates null/undefined post", () => {
  assert.deepEqual(safeCard(null), {});
  assert.deepEqual(safeCard(undefined), {});
});

// ---- search query building ----

test("searchQuery includes only non-empty filters", () => {
  assert.equal(searchQuery({ activity: " swimming ", city: "", vibe: "casual" }),
    "activity=swimming&vibe=casual");
  assert.equal(searchQuery({}), "");
  assert.equal(searchQuery(undefined), "");
});

test("searchQuery encodes unsafe characters", () => {
  const q = searchQuery({ activity: "rock & roll dancing" });
  assert.ok(!q.includes(" & "));
  assert.equal(new URLSearchParams(q).get("activity"), "rock & roll dancing");
});
