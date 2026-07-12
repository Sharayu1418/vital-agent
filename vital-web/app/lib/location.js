const GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search";

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
