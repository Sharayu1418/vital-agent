/* The whole day on one page — http://localhost:3000/sky-preview
 *
 * DEVELOPMENT ONLY. This is a tool for judging the palette, not a product
 * surface, and it 404s in a production build. It was briefly a public route
 * on a deployed app, which is a page a recruiter could land on and a page
 * that has nothing to do with VITAL.
 *
 * A server component on purpose: skyColor is pure and there are no hooks, so
 * none of this needs to run in the browser, and the palette never reaches a
 * client bundle.
 *
 * To be accurate about what the gate does: the route still EXISTS in a
 * production build — `next build` lists it at ~123 B — and is prerendered as
 * the not-found page. It returns 404, which is the point, but "absent from
 * the bundle" would be an overclaim. Verify with `pnpm build && pnpm start`
 * then curl the path; do not take this comment's word for it.
 *
 * A route rather than a standalone HTML file. The first version was
 * scripts/sky-preview.html opened with file://, where the browser refuses an
 * ES module import as a cross-origin request: the page rendered its heading
 * and the script silently never ran, so the bands were simply absent. Being
 * a route means it imports sky.js the same way the app does and cannot drift
 * from it.
 *
 * The tests prove every number in this palette. None of them can say whether
 * it looks good, which is the only reason this exists.
 *
 * What to look for:
 *   - a band that looks muddy, or green where it should be pale
 *   - text that gets tiring rather than just technically legible
 *   - an accent that disappears into its own background
 *   - visible steps instead of a gradient
 */
import { notFound } from "next/navigation";

import { contrastRatio, skyColor } from "../lib/sky";

// Dense near the horizon, where all the interesting colour happens.
const ALTITUDES = [];
for (let a = 80; a > 12; a -= 4) ALTITUDES.push(a);
for (let a = 12; a >= -20; a -= 0.5) ALTITUDES.push(Number(a.toFixed(1)));

export default function SkyPreview() {
  if (process.env.NODE_ENV === "production") notFound();

  return (
    <main style={{ font: "13px/1.4 -apple-system, Segoe UI, Roboto, sans-serif" }}>
      <div style={{ padding: "14px 16px", background: "#fff", color: "#111" }}>
        <h1 style={{ font: "600 15px/1.4 inherit", margin: 0 }}>
          VITAL sky palette — sun altitude from overhead to night
        </h1>
        <p style={{ margin: "6px 0 0", color: "#666", maxWidth: "70ch" }}>
          Rendered from the same <code>app/lib/sky.js</code> the app and the
          mobile client use. Body text targets 7:1 and is guaranteed 4.5:1;
          the ratios on the right are measured, not assumed.
        </p>
      </div>

      {ALTITUDES.map((alt) => {
        const sky = skyColor(alt);
        const body = contrastRatio(sky.bg, sky.text).toFixed(1);
        const muted = contrastRatio(sky.bg, sky.muted).toFixed(1);
        return (
          <div key={alt} style={{
            display: "flex", alignItems: "center", gap: 18,
            padding: "9px 16px", background: sky.bg, color: sky.text,
          }}>
            <span style={{ width: 66, fontVariantNumeric: "tabular-nums",
                           opacity: 0.85 }}>{alt.toFixed(1)}°</span>
            <span style={{ background: sky.panel, padding: "5px 10px",
                           borderRadius: 8 }}>panel surface</span>
            <span>Body text on this sky</span>
            <span style={{ color: sky.muted }}>secondary text</span>
            <span style={{ width: 15, height: 15, borderRadius: "50%",
                           background: sky.accent, display: "inline-block" }} />
            <span style={{ marginLeft: "auto", fontSize: 11, opacity: 0.75,
                           fontVariantNumeric: "tabular-nums" }}>
              {body}:1 · {muted}:1 · {sky.bg}
            </span>
          </div>
        );
      })}
    </main>
  );
}
