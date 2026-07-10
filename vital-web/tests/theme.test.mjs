import test from "node:test";
import assert from "node:assert/strict";

import { DAILY_LINES, dailyLine, firstNameFrom, themeForHour } from "../app/lib/theme.js";

test("mornings are light, the rest of the day is dark", () => {
  assert.equal(themeForHour(5), "light");
  assert.equal(themeForHour(9), "light");
  assert.equal(themeForHour(11), "light");
  assert.equal(themeForHour(12), "dark");
  assert.equal(themeForHour(17), "dark");
  assert.equal(themeForHour(23), "dark");
  assert.equal(themeForHour(0), "dark");
  assert.equal(themeForHour(4), "dark");
});

test("daily line is stable within a day and drawn from the list", () => {
  const d = new Date("2026-07-09T08:00:00Z");
  const later = new Date("2026-07-09T22:00:00Z");
  assert.equal(dailyLine(d), dailyLine(later));
  assert.ok(DAILY_LINES.includes(dailyLine(d)));
  // and rotates across days
  const week = new Set();
  for (let i = 0; i < DAILY_LINES.length; i += 1) {
    week.add(dailyLine(new Date(d.getTime() + i * 86400000)));
  }
  assert.equal(week.size, DAILY_LINES.length);
});

test("first name is extracted, capitalized, and sanitized", () => {
  assert.equal(firstNameFrom("  sharayu rasal "), "Sharayu");
  assert.equal(firstNameFrom("o'brien"), "O'brien");
  assert.equal(firstNameFrom("émile"), "Émile");
  assert.equal(firstNameFrom("x".repeat(50)), "X" + "x".repeat(23));
});

test("unusable names come back empty so the greeting stays nameless", () => {
  assert.equal(firstNameFrom(""), "");
  assert.equal(firstNameFrom("   "), "");
  assert.equal(firstNameFrom("123 456"), "");
  assert.equal(firstNameFrom(null), "");
  assert.equal(firstNameFrom(undefined), "");
});

test("markup characters are stripped, letters survive", () => {
  assert.equal(firstNameFrom("<script>"), "Script");   // no <> ever survive
  assert.equal(firstNameFrom("sam!!"), "Sam");
});
