import test from "node:test";
import assert from "node:assert/strict";

import {
  DAILY_LINES, clearGeo, dailyLine, daylightPhase, daylightTheme, firstNameFrom,
  readGeo, resolveTheme, roundCoord, sunTimesUTC, themeForHour, themeForPhase,
  timeGreeting, writeGeo,
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

// ---------- location-aware daylight ----------

test("sunTimesUTC gives a plausible sunrise before sunset", () => {
  // NYC, 21 Jun 2026 (summer solstice-ish): long day, sunrise ~09:25 UTC
  const { sunriseMs, sunsetMs, polar } = sunTimesUTC(
    new Date("2026-06-21T12:00:00Z"), 40.71, -74.01);
  assert.equal(polar, null);
  assert.ok(sunriseMs < sunsetMs);
  const riseH = new Date(sunriseMs).getUTCHours();
  const setH = new Date(sunsetMs).getUTCHours();
  assert.ok(riseH >= 8 && riseH <= 11, `sunrise hour ${riseH}`);   // ~09:25 UTC
  assert.ok(setH >= 23 || setH <= 1, `sunset hour ${setH}`);       // ~00:31 UTC
});

test("sunTimesUTC reports polar day/night at extreme latitude", () => {
  // Svalbard midsummer: sun never sets
  assert.equal(sunTimesUTC(new Date("2026-06-21T12:00:00Z"), 78.2, 15.6).polar, "day");
  // ...and polar night at midwinter
  assert.equal(sunTimesUTC(new Date("2026-12-21T12:00:00Z"), 78.2, 15.6).polar, "night");
});

test("daylightPhase names night, sunrise, day, and sunset windows", () => {
  const sunrise = Date.parse("2026-07-09T06:00:00Z");
  const sunset = Date.parse("2026-07-09T18:00:00Z");
  const at = (t) => daylightPhase(Date.parse(`2026-07-09T${t}:00Z`), sunrise, sunset);
  assert.equal(at("03:00"), "night");     // before sunrise
  assert.equal(at("06:20"), "sunrise");   // golden window after sunrise
  assert.equal(at("12:00"), "day");       // midday
  assert.equal(at("17:30"), "sunset");    // golden window before sunset
  assert.equal(at("21:00"), "night");     // after dusk
  // the 60-min dawn window opens at 05:00; just before it is still night
  assert.equal(at("04:50"), "night");
  assert.equal(at("05:10"), "sunrise");
});

test("themeForPhase: only night is dark", () => {
  assert.equal(themeForPhase("night"), "dark");
  assert.equal(themeForPhase("sunrise"), "light");
  assert.equal(themeForPhase("day"), "light");
  assert.equal(themeForPhase("sunset"), "light");
});

test("daylightTheme maps a place+instant to theme and phase", () => {
  // midnight local-ish in NYC (05:00 UTC) → dark/night
  const night = daylightTheme(Date.parse("2026-07-09T05:00:00Z"), 40.71, -74.01);
  assert.equal(night.theme, "dark");
  assert.equal(night.phase, "night");
  // early afternoon UTC → daylight
  const day = daylightTheme(Date.parse("2026-07-09T17:00:00Z"), 40.71, -74.01);
  assert.equal(day.theme, "light");
  assert.equal(day.phase, "day");
});

test("daylightTheme uses the location's solar date around UTC midnight", () => {
  // 00:10 UTC is still 20:10 in New York on the previous calendar day.
  // Sunset is around 00:29 UTC, so this must remain in the sunset phase
  // instead of jumping ahead to tomorrow's sunrise and reporting night.
  const dusk = daylightTheme(Date.parse("2026-07-10T00:10:00Z"), 40.71, -74.01);
  assert.equal(dusk.theme, "light");
  assert.equal(dusk.phase, "sunset");
});

test("resolveTheme uses daylight with a location, hour fallback without", () => {
  // no location → hour-based fallback (07:00 local = light morning)
  const morning = new Date("2026-07-09T07:00:00").getTime();
  assert.equal(resolveTheme(morning, null).theme, "light");
  const midnight = new Date("2026-07-09T01:00:00").getTime();
  assert.equal(resolveTheme(midnight, null).theme, "dark");
  // with a location it defers to real sun angle
  const r = resolveTheme(Date.parse("2026-07-09T17:00:00Z"), { lat: 40.71, lng: -74.01 });
  assert.equal(r.theme, "light");
  assert.equal(r.phase, "day");
});

test("geo preference round-trips and is coarsened for privacy", () => {
  const store = new Map();
  const s = {
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => store.set(k, v),
    removeItem: (k) => store.delete(k),
  };
  assert.equal(readGeo(s), null);
  writeGeo(s, 40.7128, -74.0060);
  assert.deepEqual(readGeo(s), { lat: 40.71, lng: -74.01 });   // rounded to 2dp
  writeGeo(s, 40.7128, -74.0060, { label: "New York, NY", source: "manual" });
  assert.deepEqual(readGeo(s), {
    lat: 40.71, lng: -74.01, label: "New York, NY", source: "manual",
  });
  assert.equal(roundCoord(1.23456), 1.23);
  clearGeo(s);
  assert.equal(readGeo(s), null);
});

test("readGeo tolerates garbage and partial data", () => {
  const mk = (v) => ({ getItem: () => v, setItem: () => {} });
  assert.equal(readGeo(mk("{not json")), null);
  assert.equal(readGeo(mk('{"lat":40}')), null);          // missing lng
  assert.equal(readGeo(mk('{"lat":"x","lng":1}')), null); // wrong type
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
