import test from "node:test";
import assert from "node:assert/strict";

import {
  applyEvent, initialStream, parseFrame, shouldKeepBubble,
} from "../app/lib/stream.js";

test("data lines strip exactly one space — leading spaces survive ('Iunderstand' bug)", () => {
  assert.deepEqual(parseFrame("event: token\ndata:  understand"),
    { event: "token", data: " understand" });
  assert.deepEqual(parseFrame("event: token\ndata: I"),
    { event: "token", data: "I" });
});

test("REGRESSION: token stream survives empty message + done events", () => {
  let st = initialStream();
  st = applyEvent(st, parseFrame("event: token\ndata: I"));
  st = applyEvent(st, parseFrame("event: token\ndata:  understand"));
  st = applyEvent(st, { event: "message", data: "" });   // empty terminal
  st = applyEvent(st, { event: "done", data: "" });
  assert.equal(st.text, "I understand");
  assert.ok(shouldKeepBubble(st));                        // bubble stays
});

test("non-empty message event replaces text (approve/reject confirmations)", () => {
  let st = initialStream();
  st = applyEvent(st, { event: "message", data: "Done — 2 events on your calendar." });
  assert.equal(st.text, "Done — 2 events on your calendar.");
});

test("status shows during tools, clears when tokens resume", () => {
  let st = initialStream();
  st = applyEvent(st, { event: "status", data: "sleep_energy: using get_weather" });
  assert.equal(st.status, "sleep_energy: using get_weather");
  st = applyEvent(st, { event: "token", data: "Here" });
  assert.equal(st.status, null);
});

test("approval_required captures plan and keeps flag", () => {
  let st = initialStream();
  st = applyEvent(st, {
    event: "approval_required",
    data: JSON.stringify({ type: "plan_approval", plan: { items: [], tradeoffs: "none" } }),
  });
  assert.ok(st.approval);
  assert.deepEqual(st.plan, { items: [], tradeoffs: "none" });
  assert.ok(!shouldKeepBubble(st)); // no text: placeholder may be dropped
});

test("truly empty stream drops the bubble", () => {
  let st = initialStream();
  st = applyEvent(st, { event: "done", data: "" });
  assert.ok(!shouldKeepBubble(st));
});

test("multi-line data joins with newline", () => {
  assert.deepEqual(parseFrame("event: token\ndata: line1\ndata: line2"),
    { event: "token", data: "line1\nline2" });
});
