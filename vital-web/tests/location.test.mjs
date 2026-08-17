import test from "node:test";
import assert from "node:assert/strict";

import {
  fetchElevation, formatLocationLabel, resolveElevation, geocodeLocation, shouldRequestDeviceLocation,
} from "../app/lib/location.js";

test("device location is requested only in the signed-in app", () => {
  const base = { gate: "app", location: null, available: true, permission: "prompt" };
  assert.equal(shouldRequestDeviceLocation(base), true);
  assert.equal(shouldRequestDeviceLocation({ ...base, gate: "login" }), false);
  assert.equal(shouldRequestDeviceLocation({ ...base, location: { lat: 1, lng: 2 } }), false);
  assert.equal(shouldRequestDeviceLocation({ ...base, available: false }), false);
  assert.equal(shouldRequestDeviceLocation({ ...base, permission: "denied" }), false);
  assert.equal(shouldRequestDeviceLocation({ ...base, permission: "granted" }), true);
});

test("formatLocationLabel removes empty and duplicate parts", () => {
  assert.equal(formatLocationLabel({
    name: "Albany", admin1: "New York", country: "United States",
  }), "Albany, New York, United States");
  assert.equal(formatLocationLabel({ name: "Singapore", country: "Singapore" }), "Singapore");
});

test("geocodeLocation normalizes the first valid result", async () => {
  let requested;
  const fakeFetch = async (url) => {
    requested = new URL(url);
    return {
      ok: true,
      json: async () => ({ results: [{
        name: "Albany", admin1: "New York", country: "United States",
        latitude: 42.6512, longitude: -73.755,
      }] }),
    };
  };

  assert.deepEqual(await geocodeLocation(" Albany, NY ", fakeFetch), {
    lat: 42.6512,
    lng: -73.755,
    label: "Albany, New York, United States",
    source: "manual",
  });
  assert.equal(requested.hostname, "geocoding-api.open-meteo.com");
  assert.equal(requested.searchParams.get("name"), "Albany, NY");
  assert.equal(requested.searchParams.get("count"), "1");
});

test("geocodeLocation reports invalid, missing, and failed searches", async () => {
  await assert.rejects(() => geocodeLocation("x", async () => ({})),
    /Enter a city/);
  await assert.rejects(() => geocodeLocation("Nowhere", async () => ({
    ok: true, json: async () => ({ results: [] }),
  })), /couldn't find/);
  await assert.rejects(() => geocodeLocation("Albany", async () => {
    throw new Error("offline");
  }), /unavailable/);
});


test("a stale device fix triggers a refresh, but only silently", () => {
  const now = Date.parse("2026-08-16T12:00:00Z");
  const stale = { lat: 42.65, lng: -73.76, source: "device", at: now - 30 * 3600e3 };
  const base = { gate: "app", available: true, nowMs: now };

  // permission already granted: refresh without a prompt
  assert.equal(shouldRequestDeviceLocation(
    { ...base, location: stale, permission: "granted" }), true);

  // NOT granted: leave it alone. A permission prompt on every visit trains
  // people to deny it, and then the theme falls back to the clock forever —
  // strictly worse than a stale fix.
  assert.equal(shouldRequestDeviceLocation(
    { ...base, location: stale, permission: "prompt" }), false);
  assert.equal(shouldRequestDeviceLocation(
    { ...base, location: stale, permission: undefined }), false);
  assert.equal(shouldRequestDeviceLocation(
    { ...base, location: stale, permission: "denied" }), false);
});

test("a fresh fix and a manual choice are both left alone", () => {
  const now = Date.parse("2026-08-16T12:00:00Z");
  const base = { gate: "app", available: true, permission: "granted", nowMs: now };
  assert.equal(shouldRequestDeviceLocation({
    ...base, location: { lat: 1, lng: 2, source: "device", at: now - 60e3 },
  }), false);
  assert.equal(shouldRequestDeviceLocation({
    ...base, location: { lat: 1, lng: 2, source: "manual", at: 0 },
  }), false);
});


test("elevation comes free with a geocoded location", () => {
  // Open-Meteo already returns terrain height; the app was discarding it.
  const stub = async () => ({
    ok: true,
    json: async () => ({ results: [{ latitude: 39.74, longitude: -104.98,
                                     elevation: 1609, name: "Denver",
                                     admin1: "Colorado", country: "United States" }] }),
  });
  return geocodeLocation("Denver", stub).then((result) => {
    assert.equal(result.elevationM, 1609);
    assert.equal(result.source, "manual");
  });
});

test("a geocode result without elevation is still usable", async () => {
  const stub = async () => ({
    ok: true,
    json: async () => ({ results: [{ latitude: 1, longitude: 2, name: "Nowhere" }] }),
  });
  const result = await geocodeLocation("Nowhere", stub);
  assert.equal("elevationM" in result, false);   // absent, not null or NaN
  assert.equal(result.lat, 1);
});

test("fetchElevation parses the documented shape", async () => {
  const stub = async () => ({ ok: true, json: async () => ({ elevation: [1614] }) });
  assert.equal(await fetchElevation(39.74, -104.98, stub), 1614);
});

test("fetchElevation returns null rather than breaking the theme", async () => {
  // Every failure mode degrades to sea level, which is what the app did
  // before elevation existed. A background colour must never depend on a
  // third party being up.
  const cases = [
    async () => { throw new Error("offline"); },
    async () => ({ ok: false, json: async () => ({}) }),
    async () => ({ ok: true, json: async () => ({}) }),
    async () => ({ ok: true, json: async () => ({ elevation: [] }) }),
    async () => ({ ok: true, json: async () => ({ elevation: "high" }) }),
    async () => ({ ok: true, json: async () => { throw new Error("not json"); } }),
  ];
  for (const stub of cases) {
    assert.equal(await fetchElevation(1, 2, stub), null);
  }
});

test("fetchElevation accepts a bare number too", async () => {
  // Defensive: one reading of the docs says {"elevation":[n]}. If that ever
  // becomes {"elevation":n} the theme should lose nothing.
  const stub = async () => ({ ok: true, json: async () => ({ elevation: 1614 }) });
  assert.equal(await fetchElevation(39.74, -104.98, stub), 1614);
});


// ---------- "we don't know" must not look like "sea level" ----------

test("a device altitude is used without any network call", async () => {
  // Phones outdoors report altitude directly. Free, and nothing leaves the
  // browser — strictly better than a lookup when it is available.
  const never = async () => { throw new Error("must not be called"); };
  const got = await resolveElevation(
    { coords: { latitude: 39.74, longitude: -104.98, altitude: 1610 } }, never);
  assert.deepEqual(got, { elevationM: 1610, elevationSource: "device" });
});

test("no device altitude falls back to a lookup, and says so", async () => {
  const stub = async () => ({ ok: true, json: async () => ({ elevation: [1614] }) });
  const got = await resolveElevation(
    { coords: { latitude: 39.74, longitude: -104.98, altitude: null } }, stub);
  assert.deepEqual(got, { elevationM: 1614, elevationSource: "lookup" });
});

test("a failed lookup records that the height is UNKNOWN, not zero", async () => {
  // THE point of elevationSource. Sea level is still what gets used, and that
  // is correct — but a stored location with no elevation would otherwise be
  // indistinguishable from one that genuinely sits at sea level. Somebody in
  // Denver would get a theme seven minutes off with nothing recording why.
  // That is the same silent-fallback shape as the memory writes that vanished
  // into a bare `except` and the CORS test that passed while the browser
  // failed, so it gets a marker and a console warning.
  const dead = async () => { throw new Error("offline"); };
  const warnings = [];
  const realWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    const got = await resolveElevation(
      { coords: { latitude: 39.74, longitude: -104.98, altitude: null } }, dead);
    assert.deepEqual(got, { elevationM: null, elevationSource: null });
  } finally {
    console.warn = realWarn;
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /sea level/);
});

test("a nonsense device altitude is ignored rather than trusted", async () => {
  const stub = async () => ({ ok: true, json: async () => ({ elevation: [1614] }) });
  for (const altitude of [NaN, -9999, undefined]) {
    const got = await resolveElevation(
      { coords: { latitude: 39.74, longitude: -104.98, altitude } }, stub);
    assert.equal(got.elevationSource, "lookup", `altitude ${String(altitude)}`);
  }
});
