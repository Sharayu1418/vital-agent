import { isLocationStale } from "./theme.js";

const GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search";

/* Ask the device for a position when we have none — or when the one we have
 * is an old device fix.
 *
 * The refresh branch requires permission to be ALREADY granted. Re-asking is
 * the one thing worse than a stale fix: a permission prompt on every visit
 * trains people to deny it, and then the theme falls back to the clock
 * forever. A browser with no Permissions API reports undefined, which does
 * not equal "granted", so those simply keep the old behaviour of asking once.
 */
export function shouldRequestDeviceLocation({ gate, location, available, permission,
                                              nowMs = Date.now() }) {
  if (gate !== "app" || !available || permission === "denied") return false;
  if (!location) return true;
  return permission === "granted" && isLocationStale(location, nowMs);
}

const ELEVATION_URL = "https://api.open-meteo.com/v1/elevation";

/* Terrain height for a device fix, so the sunrise maths can drop the horizon.
 *
 * A manual location gets this free — the geocoding response already carries
 * `elevation` and we were discarding it. A device fix has coordinates and
 * nothing else, so it needs one lookup, once, cached with the position.
 *
 * Returns null on ANY failure and the caller carries on at sea level, which
 * is what the app assumed until now. Being 7 minutes late to turn the
 * background orange is not worth a visible error, and the theme must never
 * depend on a network call to render.
 */
export async function fetchElevation(lat, lng, fetchImpl = fetch) {
  const url = new URL(ELEVATION_URL);
  url.searchParams.set("latitude", String(lat));
  url.searchParams.set("longitude", String(lng));
  try {
    const response = await fetchImpl(url.toString(),
                                     { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    const body = await response.json();
    // Documented shape is {"elevation":[123.0]}. Accept a bare number too
    // rather than trusting one reading of the docs — a shape change should
    // cost accuracy, not throw inside a theme.
    const value = Array.isArray(body?.elevation) ? body.elevation[0]
      : body?.elevation;
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

export function formatLocationLabel(result) {
  const parts = [result?.name, result?.admin1, result?.country]
    .filter((part) => typeof part === "string" && part.trim());
  return [...new Set(parts)].join(", ");
}

export async function geocodeLocation(query, fetchImpl = fetch) {
  const name = String(query ?? "").trim();
  if (name.length < 2) throw new Error("Enter a city or place name.");

  const url = new URL(GEOCODING_URL);
  url.searchParams.set("name", name);
  url.searchParams.set("count", "1");
  url.searchParams.set("language", "en");
  url.searchParams.set("format", "json");

  let response;
  try {
    response = await fetchImpl(url.toString(), { headers: { Accept: "application/json" } });
  } catch {
    throw new Error("Location search is unavailable. Check your connection and try again.");
  }
  if (!response.ok) throw new Error("Location search is unavailable. Try again shortly.");

  const body = await response.json().catch(() => ({}));
  const result = body.results?.find((item) =>
    Number.isFinite(item?.latitude) && Number.isFinite(item?.longitude));
  if (!result) throw new Error("We couldn't find that place. Try adding a state or country.");

  return {
    lat: result.latitude,
    lng: result.longitude,
    label: formatLocationLabel(result) || name,
    source: "manual",
    // Open-Meteo already returns terrain height and we were throwing it away.
    // It is the input the sunrise maths was missing: -0.833° assumes a
    // sea-level horizon, which put Denver's sunrise 7 minutes late. Free
    // here — no extra request, no extra coordinate leaving the browser.
    ...(Number.isFinite(result.elevation) ? { elevationM: result.elevation } : {}),
  };
}
