/**
 * The sky palette: one function, both platforms.
 *
 * WHY THIS EXISTS
 * ---------------
 * The web theme had four phases — night, sunrise, day, sunset — driving a
 * single --sky-glow multiplier over fixed palettes. The mobile app had
 * twenty-odd hardcoded hex values and was permanently dark. They could not
 * match because there was nothing shared to match ON.
 *
 * Copying a palette into both would drift the first time somebody tweaked
 * one. So the shared thing is the FUNCTION, not the colours: give it the
 * sun's altitude and it returns the same hex on every platform.
 *
 * WHY ALTITUDE RATHER THAN PHASES
 * -------------------------------
 * Sky colour is driven by how far the sun is above or below the horizon, not
 * by whether it has crossed it. That is why every twilight definition is an
 * angle. Phases also snap: the theme jumped at a boundary because there was
 * nothing between the buckets. An angle is continuous, so the sky can be.
 *
 * It also answers the skyline question. A building hides the sun's DISC; it
 * barely changes the colour overhead, because the lit sky is still lit. So
 * obstruction matters for "can I see the sunset", not for "what colour is
 * it", and the theme does not need a horizon profile to be right.
 *
 * WHY OKLCH
 * ---------
 * Interpolating sunset orange to night blue in RGB passes through a muddy
 * grey — the midpoint of two saturated opposite hues is desaturated. OKLCH
 * is perceptually uniform, so the path stays saturated and the lightness
 * ramp looks even. Output is plain hex because React Native cannot parse an
 * oklch() string, and hex is the one format both platforms agree on.
 */

/* Anchors at the altitudes that mean something physically. Between them the
 * colour is interpolated, so these are the only numbers to argue about.
 *
 * bg/panel are OKLCH triples [lightness 0-1, chroma, hue degrees]. */
export const SKY_STOPS = [
  // altitude   bg                      panel                   accent
  [90, [0.94, 0.030, 240], [0.99, 0.015, 240], [0.55, 0.15, 250]],  // overhead
  // Low-chroma pivot. Hue runs blue 240 -> gold 70, and the SHORT way round
  // passes through 155, which is green: at full chroma the midday sky came
  // out #d2eee7, a distinctly minty blue-green. Real sky pales towards white
  // before it warms, it does not go green. Dropping chroma to near zero here
  // means the hue rotation happens while there is almost no colour to see.
  [25, [0.93, 0.008, 150], [0.98, 0.006, 150], [0.53, 0.15, 150]],
  [6, [0.90, 0.050, 70], [0.97, 0.030, 70], [0.52, 0.16, 40]],   // golden hour
  [0, [0.72, 0.130, 45], [0.88, 0.080, 50], [0.45, 0.18, 25]],   // horizon
  [-6, [0.42, 0.110, 300], [0.55, 0.080, 295], [0.72, 0.14, 330]],  // civil/blue
  [-12, [0.24, 0.070, 265], [0.33, 0.050, 265], [0.75, 0.13, 200]],  // nautical
  [-18, [0.17, 0.040, 255], [0.25, 0.030, 255], [0.78, 0.12, 190]],  // night
];

/* Below this the sky stops changing — the sun is far enough down that no
 * sunlight reaches the atmosphere above you. Astronomical twilight's end. */
export const NIGHT_ALTITUDE = -18;

// ---------- colour space ----------

function srgbGamma(x) {
  return x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
}

function srgbLinear(x) {
  return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
}

/** OKLCH -> sRGB channels in 0..1, clipped. */
export function oklchToRgb(L, C, hueDeg) {
  const h = hueDeg * (Math.PI / 180);
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);

  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;

  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ].map((v) => Math.min(1, Math.max(0, srgbGamma(Math.min(1, Math.max(0, v))))));
}

export function oklchToHex(L, C, hueDeg) {
  return `#${oklchToRgb(L, C, hueDeg)
    .map((v) => Math.round(v * 255).toString(16).padStart(2, "0"))
    .join("")}`;
}

/** WCAG relative luminance of a #rrggbb string. */
export function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) =>
    srgbLinear(Number.parseInt(hex.slice(i, i + 2), 16) / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio, 1 (identical) to 21 (black on white). */
export function contrastRatio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// ---------- interpolation ----------

function lerp(a, b, t) {
  return a + (b - a) * t;
}

/** Hue is circular: 350° to 10° must travel 20° forward, not 340° back. */
function lerpHue(a, b, t) {
  let delta = ((b - a) % 360 + 540) % 360 - 180;
  return (a + delta * t + 360) % 360;
}

function lerpOklch(from, to, t) {
  return [lerp(from[0], to[0], t), lerp(from[1], to[1], t),
          lerpHue(from[2], to[2], t)];
}

/**
 * The palette for a given sun altitude, in degrees above the horizon.
 *
 * Text colour is DERIVED from the background rather than interpolated
 * independently. A freely-chosen text colour crossing a freely-chosen
 * background will, somewhere in the sweep, produce unreadable contrast —
 * and it will be at some in-between angle nobody screenshots. Deriving it
 * makes the failure impossible rather than unlikely; a test sweeps the whole
 * range anyway, because "impossible" has been wrong before.
 */
export function skyColor(altitudeDeg) {
  const alt = Number.isFinite(altitudeDeg) ? altitudeDeg : NIGHT_ALTITUDE;

  let lower = SKY_STOPS[SKY_STOPS.length - 1];
  let upper = SKY_STOPS[0];
  for (let i = 0; i < SKY_STOPS.length - 1; i += 1) {
    if (alt <= SKY_STOPS[i][0] && alt >= SKY_STOPS[i + 1][0]) {
      upper = SKY_STOPS[i];
      lower = SKY_STOPS[i + 1];
      break;
    }
  }
  if (alt >= SKY_STOPS[0][0]) { upper = SKY_STOPS[0]; lower = SKY_STOPS[0]; }
  if (alt <= NIGHT_ALTITUDE) {
    upper = SKY_STOPS[SKY_STOPS.length - 1];
    lower = upper;
  }

  const span = upper[0] - lower[0];
  const t = span === 0 ? 0 : (upper[0] - alt) / span;

  const bg = lerpOklch(upper[1], lower[1], t);
  const panel = lerpOklch(upper[2], lower[2], t);
  const accent = lerpOklch(upper[3], lower[3], t);

  const bgHex = oklchToHex(...bg);
  const light = bg[0] > 0.5;

  return {
    altitude: alt,
    bg: bgHex,
    panel: oklchToHex(...panel),
    text: readableInk(bgHex, bg[2], light, TEXT_IDEAL),
    muted: readableInk(bgHex, bg[2], light, MUTED_FLOOR),
    accent: oklchToHex(...accent),
    // "dark" | "light" — kept so existing CSS keyed on data-theme still works
    scheme: light ? "light" : "dark",
  };
}

/* Body text AIMS at AAA and is GUARANTEED AA. Those are different promises
 * and the difference is forced by arithmetic, not by taste.
 *
 * WCAG contrast against a background of luminance Y can be at most
 * max((Y + 0.05) / 0.05, 1.05 / (Y + 0.05)) — black text or white text,
 * whichever wins. Solving that for 7:1 needs Y >= 0.30 or Y <= 0.10. Between
 * those, 7:1 is unreachable no matter what colour the text is, and
 * mid-lightness sunset backgrounds sit precisely in the gap.
 *
 * The same arithmetic at 4.5:1 gives Y >= 0.175 or Y <= 0.183 — overlapping
 * ranges, so AA is always achievable for any background whatsoever.
 *
 * So: 7 where the sky allows it, 4.5 everywhere, and a test that fails if
 * body text ever drops below the floor. Aiming at 4.5 alone gave mid-grey on
 * near-black — legible by the letter of the rule and tiring to read. */
export const TEXT_IDEAL = 7.0;
export const TEXT_FLOOR = 4.5;
export const MUTED_FLOOR = 4.5;

/**
 * The SOFTEST ink on this background that still clears a contrast ratio.
 *
 * The first version picked text lightness by a simple `bg > 0.5 ? dark :
 * light` flip. That produced 3.97:1 at -3° altitude — below AA, and sitting
 * exactly at the crossover where the background is mid-lightness and neither
 * direction has much room. It is also the one moment nobody screenshots: a
 * few minutes after sunset, between the two colours anyone would check.
 *
 * Searching outward from the background's own lightness keeps the text as
 * close to the sky as legibility allows, instead of slamming to black or
 * white the moment things get tight. Preferred direction first; if that side
 * cannot reach the target at all, take the other one rather than return
 * something unreadable.
 */
function readableInk(bgHex, hue, preferDark, target) {
  const chroma = 0.02;
  const directions = preferDark
    ? [[0.45, -0.01], [0.55, 0.01]]
    : [[0.55, 0.01], [0.45, -0.01]];

  // Integer stepping, and the endpoint is reached exactly.
  //
  // This was `for (let L = 0.55; L <= 1; L += 0.01)`, which accumulates to
  // 1.0000000000000004 and exits at 0.99 — so pure white was never tried.
  // At -2.9° altitude white gives 4.68:1 and the loop returned black at
  // 4.49, one hundredth below the floor. A contrast guarantee that fails by
  // 0.01 because of float accumulation is the least interesting possible
  // way to ship an unreadable screen.
  const STEPS = 100;
  let best = { hex: null, ratio: 0 };
  for (const [start, step] of directions) {
    for (let i = 0; i <= STEPS; i += 1) {
      const L = Math.min(1, Math.max(0, start + step * i));
      const candidate = oklchToHex(L, chroma, hue);
      const ratio = contrastRatio(bgHex, candidate);
      if (ratio >= target) return candidate;        // softest that clears it
      if (ratio > best.ratio) best = { hex: candidate, ratio };
      if (L === 0 || L === 1) break;                // hit the end of this side
    }
  }
  // The target was unreachable from either direction, which happens for
  // mid-luminance backgrounds at 7:1. Return the most contrast available
  // rather than something arbitrary; the caller's floor is what is actually
  // guaranteed, and a test sweeps every tenth of a degree to prove it holds.
  return best.hex ?? (luminance(bgHex) > 0.4 ? "#000000" : "#ffffff");
}
