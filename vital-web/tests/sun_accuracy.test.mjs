/**
 * How accurate is the daylight theme, measured rather than asserted.
 *
 * theme.js implements the low-precision solar equation (the NOAA/SunCalc
 * variant). "Low precision" is a real description and the question is what it
 * costs, so these are golden values from pyephem — a proper ephemeris, at sea
 * level with standard refraction (horizon -0:34, pressure disabled so it does
 * not model its own).
 *
 * MEASURED, across 8 locations x 5 dates:
 *     median  0.25 min
 *     mean    0.56 min
 *     worst   2.58 min   (Anchorage, equinox)
 *
 * Error grows with latitude and near the equinoxes, which is expected: the
 * sun crosses the horizon at a shallower angle, so a small error in its
 * computed position becomes a larger error in time.
 *
 * WHAT THIS DOES NOT COVER, roughly in order of how wrong it can make things:
 *
 *   Stale location   — was unbounded. A fix taken in Albany and reused in
 *                      London is five hours out. Now capped by
 *                      GEO_MAX_AGE_MS; see theme.test.mjs.
 *   Horizon terrain  — a hill or a tall building to the west can hide the
 *                      sunset by tens of minutes. Unmodelled, and not
 *                      fixable without elevation data for the whole sky.
 *   Altitude         — -0.833° assumes a sea-level horizon. Denver at 1600m
 *                      sees sunrise about 7 minutes earlier than this says.
 *   Device clock     — everything keys off Date.now(). A wrong clock is a
 *                      wrong theme and nothing here can detect it.
 *   Refraction       — the -0.833° is an average. Real air varies with
 *                      temperature and pressure by a minute or two.
 *   Tick interval    — the theme re-evaluates every 5 minutes, so a phase
 *                      change lands up to 5 minutes late regardless.
 *   Coordinate round — 2dp (~1.1km). Measured at 0.03 min. Negligible, and
 *                      worth keeping for the privacy it buys.
 *
 * So the astronomy is the most accurate part of this feature by a wide
 * margin, and it is the part that looks like it needs the most attention.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { roundCoord, sunTimesUTC } from "../app/lib/theme.js";

/** pyephem, sea level, horizon -0:34, pressure 0. */
const GOLDEN = [
  ["Albany NY", 42.65, -73.76, "2026-03-20", "2026-03-20T10:58:11Z", "2026-03-20T23:07:22Z"],
  ["Albany NY", 42.65, -73.76, "2026-06-21", "2026-06-21T09:17:18Z", "2026-06-22T00:36:29Z"],
  ["London", 51.51, -0.13, "2026-03-20", "2026-03-20T06:03:24Z", "2026-03-20T18:13:30Z"],
  ["London", 51.51, -0.13, "2026-06-21", "2026-06-21T03:43:07Z", "2026-06-21T20:21:32Z"],
  ["London", 51.51, -0.13, "2026-12-21", "2026-12-21T08:03:44Z", "2026-12-21T15:53:25Z"],
  ["Singapore", 1.35, 103.82, "2026-06-21", "2026-06-20T23:00:28Z", "2026-06-22T11:12:41Z"],
  ["Sydney", -33.87, 151.21, "2026-06-21", "2026-06-20T20:59:58Z", "2026-06-22T06:54:00Z"],
  ["Reykjavik", 64.15, -21.94, "2026-03-20", "2026-03-20T07:28:36Z", "2026-03-20T19:43:24Z"],
];

/** Minutes between two instants, ignoring which calendar day they fall on.
 *  Sunrise "for a date" is a different day in UTC depending on longitude, and
 *  comparing raw timestamps reported a 24-hour error for Singapore and Sydney
 *  when the times themselves agreed to within a minute. */
function minutesApart(aMs, bMs) {
  const raw = Math.abs(aMs - bMs) / 60000;
  const within = raw % 1440;
  return Math.min(within, 1440 - within);
}

const TOLERANCE_MIN = 3;

test("sunrise and sunset match a real ephemeris within three minutes", () => {
  const errors = [];
  for (const [place, lat, lng, date, sunrise, sunset] of GOLDEN) {
    const noon = Date.parse(`${date}T12:00:00Z`);
    // the solar-date shift daylightTheme applies, so this tests what ships
    const solarDate = new Date(noon + (lng / 360) * 86400000);
    const got = sunTimesUTC(solarDate, lat, lng);

    for (const [key, want] of [["sunriseMs", sunrise], ["sunsetMs", sunset]]) {
      const err = minutesApart(got[key], Date.parse(want));
      errors.push(err);
      assert.ok(err < TOLERANCE_MIN,
        `${place} ${date} ${key}: ${err.toFixed(2)} min from the ephemeris`);
    }
  }
  const mean = errors.reduce((a, b) => a + b, 0) / errors.length;
  assert.ok(mean < 1, `mean error ${mean.toFixed(2)} min — the algorithm has ` +
    "drifted well beyond what was measured when these values were taken");
});

test("rounding coordinates for privacy costs almost nothing", () => {
  // 2dp is ~1.1km. Longitude moves solar time by 4 minutes per degree, so
  // 0.01 degrees is under 3 seconds. Worth confirming, because it is the one
  // place the app deliberately degrades its own input.
  const date = new Date("2026-08-16T12:00:00Z");
  for (const [lat, lng] of [[42.6547, -73.7562], [64.1466, -21.9426],
                            [1.3521, 103.8198], [51.5074, -0.1278]]) {
    const exact = sunTimesUTC(date, lat, lng);
    const rounded = sunTimesUTC(date, roundCoord(lat), roundCoord(lng));
    for (const key of ["sunriseMs", "sunsetMs"]) {
      assert.ok(minutesApart(exact[key], rounded[key]) < 0.5,
        `rounding moved ${key} by more than half a minute at ${lat},${lng}`);
    }
  }
});

test("the poles report no sunrise rather than a wrong one", () => {
  const midsummer = new Date("2026-06-21T12:00:00Z");
  const midwinter = new Date("2026-12-21T12:00:00Z");
  assert.equal(sunTimesUTC(midsummer, 85, 0).polar, "day");
  assert.equal(sunTimesUTC(midwinter, 85, 0).polar, "night");
  assert.equal(sunTimesUTC(midsummer, 85, 0).sunriseMs, null);
});
