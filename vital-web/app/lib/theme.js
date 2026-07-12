/* Time-of-day theming + greeting personalization. Pure and node-testable.
 * The theme follows the user's clock: sunrise palette in the morning,
 * night palette the rest of the day. There is no manual toggle. */

export function themeForHour(hour) {
  return hour >= 5 && hour < 12 ? "light" : "dark";
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
