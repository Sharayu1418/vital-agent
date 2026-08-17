import test from "node:test";
import assert from "node:assert/strict";

import {
  formatLocationLabel, geocodeLocation, shouldRequestDeviceLocation,
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
