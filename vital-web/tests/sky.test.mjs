/**
 * The sky palette: correctness, continuity, and readability.
 *
 * The colour maths is easy to get subtly wrong and impossible to eyeball —
 * a palette that looks fine in the two screenshots anybody takes can be
 * unreadable at some angle in between. So the interesting tests here sweep
 * the whole range rather than checking a few named values.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  MUTED_FLOOR, NIGHT_ALTITUDE, SKY_STOPS, TEXT_FLOOR, TEXT_IDEAL,
  contrastRatio, luminance, oklchToHex, skyColor,
} from "../app/lib/sky.js";

// ---------- the colour space, against published values ----------

test("OKLCH converts to the sRGB primaries exactly", () => {
  // If this drifts, every colour below is wrong in a way no eye would catch
  // from a hex string. These are the standard OKLCH coordinates of the sRGB
  // corners.
  assert.equal(oklchToHex(1, 0, 0), "#ffffff");
  assert.equal(oklchToHex(0, 0, 0), "#000000");
  assert.equal(oklchToHex(0.6280, 0.2577, 29.23), "#ff0000");
  assert.equal(oklchToHex(0.8664, 0.2948, 142.50), "#00ff00");
  assert.equal(oklchToHex(0.4520, 0.3132, 264.05), "#0000ff");
});

test("luminance and contrast match the WCAG definition", () => {
  assert.equal(luminance("#000000"), 0);
  assert.equal(luminance("#ffffff"), 1);
  assert.equal(contrastRatio("#000000", "#ffffff"), 21);
  assert.equal(contrastRatio("#123456", "#123456"), 1);
});

// ---------- readability, swept ----------

test("text stays readable at EVERY sun altitude", () => {
  // THE test. A background that changes continuously will, somewhere,
  // produce unreadable text unless it is checked continuously. The first
  // implementation flipped text at bg lightness 0.5 and hit 3.97:1 just
  // after sunset — below AA, at the one moment nobody screenshots.
  const failures = [];
  for (let alt = -25; alt <= 90; alt += 0.05) {
    const sky = skyColor(alt);
    const body = contrastRatio(sky.bg, sky.text);
    const muted = contrastRatio(sky.bg, sky.muted);
    if (body < TEXT_FLOOR || muted < MUTED_FLOOR) {
      failures.push(`${alt.toFixed(2)}deg body ${body.toFixed(2)} ` +
                    `muted ${muted.toFixed(2)}`);
    }
  }
  assert.deepEqual(failures.slice(0, 5), [],
    `${failures.length} altitudes fall below the contrast floor`);
});

test("most of the range clears AAA, and the shortfall is arithmetic", () => {
  // 7:1 is impossible against a background whose luminance sits between
  // ~0.10 and ~0.30 — no text colour reaches it. Sunset backgrounds pass
  // through that band, so a demand for 7:1 everywhere would be a demand to
  // not have sunset colours. This asserts the compromise is small.
  let reaching = 0;
  let total = 0;
  for (let alt = -25; alt <= 90; alt += 0.05) {
    const sky = skyColor(alt);
    total += 1;
    if (contrastRatio(sky.bg, sky.text) >= TEXT_IDEAL) reaching += 1;
  }
  assert.ok(reaching / total > 0.9,
    `only ${(100 * reaching / total).toFixed(0)}% of altitudes reach 7:1`);
});

test("the ink search reaches pure white and pure black", () => {
  // A float-accumulating loop (0.55 += 0.01 up to 1) stopped at 0.99 and
  // never tried white. At -2.9 degrees white gives 4.68:1 and the search
  // returned black at 4.49 — one hundredth under the floor, from rounding.
  const sky = skyColor(-2.9);
  assert.ok(contrastRatio(sky.bg, sky.text) >= TEXT_FLOOR,
    "the altitude where only an endpoint colour is good enough");
});

// ---------- continuity ----------

test("the sky never jumps", () => {
  // The old four-phase theme snapped at boundaries because there was nothing
  // between the buckets. Interpolation removes the seams only if the stops
  // actually join up — a typo in SKY_STOPS would reintroduce a jump that is
  // hard to see and obvious once it is on screen.
  const channels = (hex) => [1, 3, 5].map((i) =>
    Number.parseInt(hex.slice(i, i + 2), 16));

  let worst = { delta: 0, alt: null };
  for (let alt = -20; alt <= 90; alt += 0.1) {
    const a = channels(skyColor(alt).bg);
    const b = channels(skyColor(alt + 0.1).bg);
    const delta = Math.max(...a.map((v, i) => Math.abs(v - b[i])));
    if (delta > worst.delta) worst = { delta, alt };
  }
  // 0.1 degrees is well under a minute of real time near the horizon.
  assert.ok(worst.delta <= 6,
    `background moves ${worst.delta} of 255 in a tenth of a degree at ` +
    `${worst.alt} — that is a visible step, not a gradient`);
});

test("the sky gets lighter as the sun climbs", () => {
  // Asserted on OKLCH lightness, NOT on WCAG luminance.
  //
  // The first version compared luminance of the rendered hex and failed at
  // 8 degrees: golden-hour yellow has HIGHER WCAG luminance than the pale
  // blue overhead, because the formula weights green at 0.72 and blue at
  // 0.07. Both facts are correct; they are just different quantities. The
  // invariant that actually matters is perceptual lightness, which lives in
  // the stops.
  for (let i = 0; i < SKY_STOPS.length - 1; i += 1) {
    const [highAlt, highBg] = SKY_STOPS[i];
    const [lowAlt, lowBg] = SKY_STOPS[i + 1];
    assert.ok(highBg[0] > lowBg[0],
      `stop at ${highAlt}deg is not lighter than the one at ${lowAlt}deg`);
  }
  // And the ends are unmistakably different, whatever the middle does.
  assert.ok(luminance(skyColor(60).bg) > luminance(skyColor(-18).bg) * 8);
});

test("the stops are ordered high to low", () => {
  for (let i = 0; i < SKY_STOPS.length - 1; i += 1) {
    assert.ok(SKY_STOPS[i][0] > SKY_STOPS[i + 1][0],
      "skyColor walks the stops assuming descending altitude");
  }
});

// ---------- edges ----------

test("absurd and missing altitudes give night, not a crash", () => {
  for (const bad of [undefined, null, NaN, "dusk", -Infinity]) {
    const sky = skyColor(bad);
    assert.match(sky.bg, /^#[0-9a-f]{6}$/);
    assert.equal(sky.scheme, "dark");
  }
  // Below astronomical twilight nothing more happens — no sunlight reaches
  // the sky above you, so -19 and -60 must be identical.
  assert.deepEqual(skyColor(-19), { ...skyColor(-60), altitude: -19 });
});

// ---------- one palette, two platforms ----------

/* Golden table. Both suites assert against THIS, so a change on either
 * platform that does not change the other fails immediately rather than
 * being noticed on somebody's phone. */
export const GOLDEN_SKY = [
  [90, "#daeefe"], [25, "#e7e7ee"], [10, "#fad8cc"], [6, "#f4d9bb"], [0,
  "#e7885d"], [-3, "#ac577e"], [-6, "#573c7f"], [-12, "#0e1d40"], [-18,
  "#041020"],
];

test("the palette matches the golden table", () => {
  for (const [alt, expected] of GOLDEN_SKY) {
    assert.equal(skyColor(alt).bg, expected, `altitude ${alt}`);
  }
});

test("mobile ships the identical module, not a copy that drifted", () => {
  // Expo and Next bundle from their own trees, so the file exists twice.
  // Byte equality is the cheapest possible guarantee that "the same colour
  // on both platforms" stays true — and copied palettes drift the first
  // time somebody tweaks one, which is the entire reason this is shared.
  const web = readFileSync(new URL("../app/lib/sky.js", import.meta.url), "utf8");
  const mobile = readFileSync(
    new URL("../../vital-mobile/lib/sky.js", import.meta.url), "utf8");
  assert.equal(mobile, web,
    "vital-mobile/lib/sky.js has drifted from vital-web/app/lib/sky.js — " +
    "run scripts/sync-sky.sh");
});


// ---------- does the colour actually reach the screen ----------

test("the sky variables feed the ones the app paints with", () => {
  // The gap this closes: skyColor was computed correctly, tested to four
  // decimal places, and wired to --sky-bg and --sky-text only. The app
  // paints with --muted (58 uses), --accent (50), --glass (17) and --text
  // (17). So a fully-tested continuous palette changed the body background
  // and essentially nothing else.
  //
  // Computing a colour and displaying it are different claims, and the tests
  // only covered the first one. Same shape as the CORS contract test: check
  // the seam between the two halves, not each half alone.
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

  for (const [consumer, source] of [
    ["--text", "--sky-text"],
    ["--muted", "--sky-muted"],
    ["--accent", "--sky-accent"],
    ["--surface", "--sky-panel"],
  ]) {
    // BOTH palettes, not one. The first version accepted a single match, so
    // unwiring the dark theme still passed on the strength of the light one
    // — a mutation check caught it. There are two theme blocks and a colour
    // that only follows the sun in one of them is a bug that shows up for
    // half the day.
    const pattern = new RegExp(`\\${consumer}:\\s*var\\(${source},`, "g");
    const found = (css.match(pattern) || []).length;
    assert.ok(found >= 2,
      `${consumer} reads ${source} in ${found} of the 2 theme blocks — the ` +
      "sky is computed but not painted with everywhere");
  }
});

test("every sky variable has a static fallback", () => {
  // The theme must survive no location, denied geolocation, private mode,
  // and the first paint before JS runs. A var() without a fallback renders
  // as `unset` and the app loses its colours entirely — a much louder
  // failure than the stale-elevation one, and just as avoidable.
  const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
  const uses = css.match(/var\(--sky-(?:text|muted|accent|panel|bg)[^)]*\)/g) || [];
  assert.ok(uses.length >= 8, "expected the sky variables to be consumed");
  for (const use of uses) {
    assert.match(use, /,\s*\S/,
      `${use} has no fallback — with no location this renders as unset`);
  }
});
