import test from "node:test";
import assert from "node:assert/strict";

import {
  DAILY_LINES, dailyLine, firstNameFrom, themeForHour, timeGreeting,
} from "../app/lib/theme.js";

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

test("timeGreeting matches the theme's hours and speaks VITAL's voice", () => {
  // greeting brackets align with themeForHour's morning/rest-of-day split
  assert.equal(timeGreeting(5).hi, "Good morning");
  assert.equal(timeGreeting(11).hi, "Good morning");
  assert.equal(timeGreeting(12).hi, "Good afternoon");
  assert.equal(timeGreeting(16).hi, "Good afternoon");
  assert.equal(timeGreeting(17).hi, "Good evening");
  assert.equal(timeGreeting(21).hi, "Good evening");
  assert.equal(timeGreeting(22).hi, "Up late");
  assert.equal(timeGreeting(3).hi, "Up late");
  // every bracket carries a warm, non-empty line (login + hero share this)
  for (const h of [6, 13, 19, 23]) {
    assert.ok(timeGreeting(h).line.length > 0);
  }
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
