"use client";

/* Predicted energy over the next 24h (A-1).
 *
 * Inline SVG rather than a charting library: it is one path, and the
 * bundle cost of a chart dependency for a single sparkline is not worth
 * paying. No dependencies means nothing here to keep upgraded either.
 *
 * The confidence number is rendered as prominently as the curve on
 * purpose. A forecast cannot be checked against reality on the day it is
 * made, so a curve shown without its basis reads as authoritative
 * regardless of whether it rests on fourteen logged nights or none.
 */

const W = 260;
const H = 72;
const PAD = 6;

function pathFor(points) {
  if (points.length < 2) return "";
  const xs = points.map((_, i) => PAD + (i * (W - 2 * PAD)) / (points.length - 1));
  const ys = points.map((p) => H - PAD - p.energy * (H - 2 * PAD));
  return xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
}

function clockLabel(iso) {
  // The server already rendered these in the user's local frame, so parse
  // the wall-clock fields directly. new Date() would apply the browser
  // offset a second time and shift every label.
  const time = String(iso).split("T")[1] || "";
  return time.slice(0, 5);
}

export default function EnergyCurve({ forecast }) {
  // Two points minimum. With one, every x is divided by (length - 1) = 0,
  // which puts NaN into the SVG coordinates — a chart that silently draws
  // nothing rather than an obvious failure.
  if (!forecast?.curve || forecast.curve.length < 2) return null;

  const { curve, confidence, basis, peak, dip } = forecast;
  const line = pathFor(curve);
  const area = line ? `${line} L${W - PAD},${H - PAD} L${PAD},${H - PAD} Z` : "";

  const best = curve.reduce((a, b) => (b.energy > a.energy ? b : a), curve[0]);
  const worst = curve.reduce((a, b) => (b.energy < a.energy ? b : a), curve[0]);
  const bestX = PAD + (curve.indexOf(best) * (W - 2 * PAD)) / (curve.length - 1);
  const bestY = H - PAD - best.energy * (H - 2 * PAD);
  const worstX = PAD + (curve.indexOf(worst) * (W - 2 * PAD)) / (curve.length - 1);
  const worstY = H - PAD - worst.energy * (H - 2 * PAD);

  const unsure = confidence < 0.4;

  return (
    <section className="sidebar-forecast" aria-labelledby="forecast-title">
      <div className="sidebar-memory-head">
        <h2 id="forecast-title">Predicted energy</h2>
        <span className={`forecast-conf ${unsure ? "low" : ""}`}
              title={basis}>
          {Math.round(confidence * 100)}%
        </span>
      </div>

      <svg className="forecast-chart" viewBox={`0 0 ${W} ${H}`}
           role="img"
           aria-label={
             peak && dip
               ? `Predicted energy peaks at ${peak.at} and dips at ${dip.at}`
               : "Predicted energy over the next day"
           }>
        <path className="forecast-area" d={area} />
        <path className="forecast-line" d={line} />
        <circle className="forecast-peak" cx={bestX} cy={bestY} r="3" />
        <circle className="forecast-dip" cx={worstX} cy={worstY} r="3" />
      </svg>

      <div className="forecast-marks">
        <span>{clockLabel(curve[0].at)}</span>
        <span>{clockLabel(curve[curve.length - 1].at)}</span>
      </div>

      {peak && dip && (
        <dl className="forecast-legend">
          <div><dt>Peak</dt><dd>{peak.at.split(" ")[1]} · {peak.why?.[0]}</dd></div>
          <div><dt>Dip</dt><dd>{dip.at.split(" ")[1]} · {dip.why?.[0]}</dd></div>
        </dl>
      )}

      {unsure && (
        <p className="side-hint">{basis}</p>
      )}
    </section>
  );
}
