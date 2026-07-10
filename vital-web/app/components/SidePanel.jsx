"use client";
/* Live glance panel — WHOOP-style progressive disclosure: the top tier is
 * glanceable (score-ish numbers, tiny chart); depth lives in the chat. */
import Buddies from "./Buddies";

function SleepChart({ nights, targetMin }) {
  if (!nights?.length) return null;
  const W = 280, H = 90, PAD = 4;
  const max = Math.max(targetMin, ...nights.map((n) => n.duration_min)) * 1.1;
  const bw = (W - PAD * 2) / Math.max(nights.length, 7);
  const y = (v) => H - (v / max) * H;
  const color = (m) => m >= 450 ? "var(--accent)" : m >= 360 ? "var(--amber)" : "var(--danger)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="sleep-chart" role="img"
      aria-label="Recent sleep durations">
      <line x1={PAD} x2={W - PAD} y1={y(targetMin)} y2={y(targetMin)}
        stroke="var(--muted)" strokeDasharray="4 4" strokeWidth="1" opacity="0.6" />
      {nights.map((n, i) => (
        <rect key={n.date}
          x={PAD + i * bw + bw * 0.18} width={bw * 0.64}
          y={y(n.duration_min)} height={H - y(n.duration_min)}
          rx="3" fill={color(n.duration_min)}>
          <title>{`${n.date}: ${(n.duration_min / 60).toFixed(1)}h`}</title>
        </rect>
      ))}
    </svg>
  );
}

export default function SidePanel({ sleep, events, memories, onForget, open, onClose }) {
  const nights = sleep?.nights ?? [];
  const avg = nights.length
    ? nights.reduce((a, n) => a + n.duration_min, 0) / nights.length : null;
  const debt = nights.length
    ? nights.reduce((a, n) => a + Math.max(0, (sleep.target_min ?? 480) - n.duration_min), 0)
    : null;

  return (
    <>
      {open && <div className="scrim" onClick={onClose} />}
      <aside className={`panel ${open ? "open" : ""}`}>
        <section className="card">
          <h3>Sleep</h3>
          {nights.length === 0 ? (
            <p className="side-hint">No data yet. Upload a sleep file or just
              tell VITAL how you slept.</p>
          ) : (
            <>
              <div className="stat-row">
                <div className="stat">
                  <span className="stat-num">{(avg / 60).toFixed(1)}h</span>
                  <span className="stat-label">avg / night</span>
                </div>
                <div className="stat">
                  <span className="stat-num">{(debt / 60).toFixed(1)}h</span>
                  <span className="stat-label">debt ({nights.length}d)</span>
                </div>
              </div>
              <SleepChart nights={nights} targetMin={sleep.target_min ?? 480} />
            </>
          )}
        </section>

        <section className="card">
          <h3>Your plan</h3>
          {!events?.length ? (
            <p className="side-hint">Approved plans land here. Try “plan my
              weekend”.</p>
          ) : (
            events.map((e, i) => (
              <div className="event" key={i}>
                <span className="event-when">{e.day} {e.start}</span>
                <span className="event-title">{e.title}</span>
                <span className={`kind-chip kind-${e.kind}`}>{e.kind}</span>
              </div>
            ))
          )}
        </section>

        <Buddies />

        <section className="card">
          <h3>What VITAL knows</h3>
          {!memories?.length ? (
            <p className="side-hint">It learns stable facts as you chat.
              Visible and deletable, always.</p>
          ) : (
            memories.map((m) => (
              <div className="memory-row" key={m.key}>
                <span>{m.fact}</span>
                <button className="thread-del" title="Forget"
                  onClick={() => onForget(m.key)}>×</button>
              </div>
            ))
          )}
        </section>
      </aside>
    </>
  );
}
