import test from "node:test";
import assert from "node:assert/strict";

import { formatLocationLabel, geocodeLocation } from "../app/lib/location.js";

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
