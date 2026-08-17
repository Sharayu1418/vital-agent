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
  };
}
