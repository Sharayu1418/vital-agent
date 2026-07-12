/* Time-of-day theming + greeting personalization. Pure and node-testable.
 * The theme follows the user's clock: sunrise palette in the morning,
 * night palette the rest of the day. There is no manual toggle. */

export function themeForHour(hour) {
  return hour >= 5 && hour < 12 ? "light" : "dark";
}

/* ---------- location-aware daylight ----------
 * Sunrise/sunset from latitude/longitude via the standard sunrise equation
 * (Wikipedia). Pure and deterministic — no clock, no network — so the UI
 * can reflect real daylight at the user's place, with a graceful fall back
 * to themeForHour when no location is known. */

const _RAD = Math.PI / 180;
const _DAY_MS = 86400000;
const _J1970 = 2440587.5;   // Julian date of the Unix epoch

/* { sunriseMs, sunsetMs, polar } in epoch ms (UTC). polar is "day" (sun
 * never sets) or "night" (never rises) at extreme latitudes, else null. */
export function sunTimesUTC(date, lat, lng) {
  const julian = date.valueOf() / _DAY_MS + _J1970;
  const n = Math.round(julian - 2451545.0 + 0.0008);   // days since J2000
  const jStar = n - lng / 360;                          // mean solar time
  const M = (357.5291 + 0.98560028 * jStar) % 360;      // solar mean anomaly
  const Mr = M * _RAD;
  const C = 1.9148 * Math.sin(Mr) + 0.02 * Math.sin(2 * Mr) + 0.0003 * Math.sin(3 * Mr);
  const lambda = ((M + C + 180 + 102.9372) % 360) * _RAD;   // ecliptic longitude
  const jTransit = 2451545.0 + jStar
    + 0.0053 * Math.sin(Mr) - 0.0069 * Math.sin(2 * lambda);  // solar noon
  const sinDelta = Math.sin(lambda) * Math.sin(23.4397 * _RAD);   // declination
  const cosDelta = Math.cos(Math.asin(sinDelta));
  const phi = lat * _RAD;
  // hour angle for the sun's centre at -0.833° (refraction + solar radius)
  const cosOmega = (Math.sin(-0.833 * _RAD) - Math.sin(phi) * sinDelta)
    / (Math.cos(phi) * cosDelta);
  if (cosOmega >= 1) return { sunriseMs: null, sunsetMs: null, polar: "night" };
  if (cosOmega <= -1) return { sunriseMs: null, sunsetMs: null, polar: "day" };
  const omega = Math.acos(cosOmega) / _RAD;             // degrees
  const toMs = (j) => (j - _J1970) * _DAY_MS;
  return {
    sunriseMs: toMs(jTransit - omega / 360),
    sunsetMs: toMs(jTransit + omega / 360),
    polar: null,
  };
}

/* One of "night" | "sunrise" | "day" | "sunset". The golden window
 * (default 60 min) around each event is its own phase so the sky can warm
 * at dawn/dusk. */
export function daylightPhase(nowMs, sunriseMs, sunsetMs, goldenMin = 60) {
  if (sunriseMs == null || sunsetMs == null) return "night";
  const w = goldenMin * 60000;
  if (nowMs < sunriseMs - w) return "night";
  if (nowMs < sunriseMs + w) return "sunrise";
  if (nowMs < sunsetMs - w) return "day";
  if (nowMs < sunsetMs + w) return "sunset";
  return "night";
}

export function themeForPhase(phase) {
  return phase === "night" ? "dark" : "light";
}

/* { theme, phase } for a place and instant. Polar day/night collapse to
 * day/night. Callers apply theme to data-theme and phase to data-daylight
 * (which drives subtle warmth/dimness in CSS). */
export function daylightTheme(nowMs, lat, lng) {
  // Use the place's approximate solar date. The UTC date can already be
  // tomorrow in the Americas while today's sunset is still in progress (or
  // still be yesterday east of the date line), which selects the wrong pair
  // of sun events around UTC midnight.
  const solarDate = new Date(nowMs + (lng / 360) * _DAY_MS);
  const { sunriseMs, sunsetMs, polar } = sunTimesUTC(solarDate, lat, lng);
  if (polar) return { theme: themeForPhase(polar), phase: polar };
  const phase = daylightPhase(nowMs, sunriseMs, sunsetMs);
  return { theme: themeForPhase(phase), phase };
}

/* Minimal client-side location preference: rounded to ~1km for privacy
 * (we only need it for sun angle, not to pinpoint anyone). Stored under one
 * key; readGeo tolerates absent/garbage. Works for a granted geolocation
 * fix OR a manually set location — both write the same shape. */
export const GEO_KEY = "vital_geo";

export function roundCoord(n) {
  return Math.round(n * 100) / 100;   // 2dp ≈ 1.1km
}

export function readGeo(storage) {
  try {
    const g = JSON.parse(storage.getItem(GEO_KEY) || "null");
    return (g && typeof g.lat === "number" && typeof g.lng === "number")
      ? {
          lat: g.lat,
          lng: g.lng,
          ...(typeof g.label === "string" && g.label ? { label: g.label } : {}),
          ...(g.source === "manual" || g.source === "device" ? { source: g.source } : {}),
        } : null;
  } catch {
    return null;
  }
}

export function writeGeo(storage, lat, lng, details = {}) {
  try {
    const geo = {
      lat: roundCoord(lat),
      lng: roundCoord(lng),
      ...(details.label ? { label: String(details.label).slice(0, 100) } : {}),
      ...(details.source === "manual" || details.source === "device"
        ? { source: details.source } : {}),
    };
    storage.setItem(GEO_KEY, JSON.stringify(geo));
    return geo;
  } catch { /* private mode: theme just falls back to the clock */ }
  return null;
}

export function clearGeo(storage) {
  try {
    storage.removeItem(GEO_KEY);
  } catch { /* private mode: there may be nothing to clear */ }
}

/* The one theme decision the app makes each tick: real daylight when a
 * location is known, else the local-hour fallback. Pure and testable. */
export function resolveTheme(nowMs, geo) {
  if (geo && typeof geo.lat === "number" && typeof geo.lng === "number") {
    return daylightTheme(nowMs, geo.lat, geo.lng);
  }
  const theme = themeForHour(new Date(nowMs).getHours());
  return { theme, phase: theme === "light" ? "day" : "night" };
}

/* The time-of-day greeting in VITAL's voice — shared by the chat hero and
 * the sign-in screen so the app sounds the same before and after login.
 * Nameless; callers append a name if they have one. */
export function timeGreeting(hour) {
  if (hour >= 5 && hour < 12) {
    return { hi: "Good morning", line: "Where should today's energy go?" };
  }
  if (hour >= 12 && hour < 17) {
    return { hi: "Good afternoon", line: "Time for a reset, an idea, or a plan?" };
  }
  if (hour >= 17 && hour < 22) {
    return { hi: "Good evening", line: "How did today treat you?" };
  }
  // 22:00–04:59 — the small hours read as night, matching themeForHour
  return { hi: "Up late", line: "Let's look after tomorrow-you." };
}

/* Short, gentle lines rotated once per day. Human tone, no productivity
 * guilt. Deterministic so SSR and client agree within a day. */
export const DAILY_LINES = [
  "Small steps still count.",
  "Rest is part of the plan.",
  "Energy first, everything else after.",
  "You don't have to earn your evening.",
  "One good hour beats a rushed day.",
  "Momentum loves company.",
  "Today doesn't need to be impressive.",
  "Notice what gave you energy today.",
];

export function dailyLine(date = new Date()) {
  const day = Math.floor(date.getTime() / 86400000);
  return DAILY_LINES[((day % DAILY_LINES.length) + DAILY_LINES.length) % DAILY_LINES.length];
}

/* First name only, letters (incl. accents), capitalized, capped. Returns ""
 * for anything unusable so callers can fall back to a nameless greeting. */
export function firstNameFrom(raw) {
  const word = (raw ?? "").trim().split(/\s+/)[0] ?? "";
  const clean = word.replace(/[^\p{L}'-]/gu, "").slice(0, 24);
  if (!clean) return "";
  return clean[0].toUpperCase() + clean.slice(1);
}
