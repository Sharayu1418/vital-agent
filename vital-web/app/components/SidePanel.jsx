"use client";
/* Live glance panel — WHOOP-style progressive disclosure: the top tier is
 * glanceable (score-ish numbers, tiny chart); depth lives in the chat. */
import { useEffect, useState } from "react";

import { geocodeLocation } from "../lib/location";
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

function DaylightLocation({ location, onChange }) {
  const [editing, setEditing] = useState(!location);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (location) setEditing(false);
  }, [location]);

  async function findCity(event) {
    event.preventDefault();
    setBusy("manual");
    setError(null);
    try {
      const result = await geocodeLocation(query);
      onChange(result);
      setQuery("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  }

  function useDeviceLocation() {
    if (!navigator.geolocation) {
      setError("Location access isn't available in this browser. Enter a city instead.");
      return;
    }
    setBusy("device");
    setError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        onChange({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          label: "Current location",
          source: "device",
        });
        setBusy(null);
      },
      (err) => {
        setError(err.code === 1
          ? "Location permission was declined. You can enter a city instead."
          : "We couldn't get your location. Enter a city or try again.");
        setBusy(null);
      },
      { timeout: 8000, maximumAge: 6 * 3600 * 1000 },
    );
  }

  return (
    <section className="card daylight-card">
      <h3>Daylight location</h3>
      {location && !editing ? (
        <div className="location-current">
          <div>
            <strong>{location.label || "Saved location"}</strong>
            <p className="side-hint">The interface follows sunrise and sunset here.</p>
          </div>
          <button className="location-change" onClick={() => { setEditing(true); setError(null); }}>
            Change
          </button>
        </div>
      ) : (
        <div className="location-editor">
          <p className="side-hint">Match the interface light to sunrise and sunset where you are.</p>
          <button className="location-device" onClick={useDeviceLocation}
            disabled={busy !== null}>
            {busy === "device" ? "Finding location..." : "Use my location"}
          </button>
          <div className="location-divider"><span>or enter a city</span></div>
          <form className="location-form" onSubmit={findCity}>
            <label htmlFor="daylight-city">City or place</label>
            <div className="location-search-row">
              <input id="daylight-city" value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Albany, NY" autoComplete="address-level2" />
              <button className="primary" disabled={busy !== null || query.trim().length < 2}>
                {busy === "manual" ? "Finding..." : "Set"}
              </button>
            </div>
          </form>
          {error && <p className="location-error" role="alert">{error}</p>}
          {location && (
            <div className="location-editor-actions">
              <button onClick={() => { setEditing(false); setError(null); }}>Cancel</button>
              <button onClick={() => { onChange(null); setEditing(true); }}>
                Use clock only
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default function SidePanel({
  sleep, events, memories, onForget, open, onClose, location, onLocationChange,
}) {
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

        <DaylightLocation location={location} onChange={onLocationChange} />

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
