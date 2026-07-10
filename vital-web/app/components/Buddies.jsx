"use client";
/* Activity Buddy Board — opt-in "find people to do things with".
 * Deliberately NOT a dating-app feel: compact cards, approximate areas only,
 * request-to-join instead of DMs, safety copy where people browse.
 *
 * The dialog renders through a portal to <body>: the side panel uses
 * backdrop-filter, which turns it into the containing block for
 * position:fixed children — without the portal the dialog gets trapped
 * and clipped inside the panel. Centered dialog on desktop, bottom sheet
 * on small screens (see globals.css). */
import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "../lib/api";
import {
  BUDGETS, GROUP_SIZES, REQUIRED_FIELDS, SAFETY_LINE, SAFETY_NOTE, SKILLS,
  TIME_WINDOWS, VIBES, buildPostPayload, safeCard, searchQuery,
} from "../lib/buddies";

const EMPTY_FORM = {
  display_name: "", activity: "", city: "", area: "", time_window: "",
  vibe: "", skill_level: "", budget: "", group_size: "", notes: "", active: true,
};

const LABELS = {
  display_name: "Display name", activity: "Activity", city: "City",
  area: "Neighborhood / area", time_window: "When", vibe: "Vibe",
  skill_level: "Skill level", budget: "Budget", group_size: "Group size",
  notes: "Notes",
};

const TABS = [
  { id: "create", label: "Post" },
  { id: "browse", label: "Browse" },
  { id: "requests", label: "Requests" },
];

const HEADINGS = {
  create: ["Post a plan",
    "Say what you want to do and roughly where."],
  browse: ["Find buddies",
    "Search by activity and city. You decide who joins."],
  requests: ["Requests",
    "Approve people for your posts, and track the ones you sent."],
};

function Select({ field, value, onChange, options }) {
  return (
    <label className="bud-field">
      <span>{LABELS[field]}</span>
      <select value={value} onChange={(e) => onChange(field, e.target.value)}>
        <option value="">No preference</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

function Input({ field, value, onChange, placeholder = "", hint = null }) {
  const required = REQUIRED_FIELDS.includes(field);
  return (
    <label className="bud-field">
      <span>{LABELS[field]}{required && <em className="bud-req"> required</em>}</span>
      <input value={value} placeholder={placeholder}
        onChange={(e) => onChange(field, e.target.value)} />
      {hint && <small className="bud-hint">{hint}</small>}
    </label>
  );
}

function CreateForm({ onCreated }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [note, setNote] = useState(null);
  const [busy, setBusy] = useState(false);
  // progressive disclosure (NN/g): four key fields first, the rest on request
  const [details, setDetails] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function submit() {
    const { payload, missing, ok } = buildPostPayload(form);
    if (!ok) { setNote(`Please fill in: ${missing.map((m) => LABELS[m]).join(", ")}`); return; }
    setBusy(true);
    setNote(null);
    try {
      const r = await api.buddyCreate(payload);
      if (!r.ok) {
        setNote((await r.json().catch(() => ({}))).detail ?? `Couldn't post (${r.status})`);
      } else {
        setForm(EMPTY_FORM);
        onCreated();
      }
    } catch {
      setNote("Can't reach the backend. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bud-form">
      <Input field="display_name" value={form.display_name} onChange={set}
        placeholder="Swim Sam" />
      <Input field="activity" value={form.activity} onChange={set}
        placeholder="Swimming" />
      <Input field="city" value={form.city} onChange={set} placeholder="Albany" />
      <Input field="area" value={form.area} onChange={set} placeholder="Guilderland"
        hint="Part of town, not an address" />

      {!details ? (
        <button type="button" className="bud-disclose"
          onClick={() => setDetails(true)}>+ Add details</button>
      ) : (
        <>
          <Select field="time_window" value={form.time_window} onChange={set} options={TIME_WINDOWS} />
          <Select field="vibe" value={form.vibe} onChange={set} options={VIBES} />
          <Select field="skill_level" value={form.skill_level} onChange={set} options={SKILLS} />
          <Select field="budget" value={form.budget} onChange={set} options={BUDGETS} />
          <Select field="group_size" value={form.group_size} onChange={set} options={GROUP_SIZES} />
          <label className="bud-field bud-field-wide">
            <span>{LABELS.notes}</span>
            <textarea rows={2} value={form.notes} maxLength={280}
              placeholder="Optional note"
              onChange={(e) => set("notes", e.target.value)} />
            <small className="bud-hint">Public. No exact addresses or contact info.</small>
          </label>
        </>
      )}

      {note && <p className="bud-note">{note}</p>}
      <button className="primary bud-submit" disabled={busy} onClick={submit}>
        {busy ? "Posting…" : "Post"}
      </button>
      <p className="bud-safety">{SAFETY_LINE}</p>
    </div>
  );
}

function MatchCard({ post, onBlocked }) {
  const p = safeCard(post);
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState("");
  const [name, setName] = useState("");
  const [state, setState] = useState(null); // null | "sent" | error text

  async function sendRequest() {
    try {
      const r = await api.buddyRequestJoin(p.id, message.trim(), name.trim());
      if (r.ok) {
        setState("sent");   // local only: a re-search would wipe this state
        setAsking(false);
      } else {
        setState((await r.json().catch(() => ({}))).detail ?? `Couldn't send (${r.status})`);
      }
    } catch {
      setState("Can't reach the backend.");
    }
  }

  async function report() {
    if (!window.confirm("Report this post to VITAL?")) return;
    await api.buddyReport(p.id, "reported from buddy board").catch(() => {});
    setState("Reported. Thank you.");
  }

  async function block() {
    if (!window.confirm("Block this member? You won't see each other's posts.")) return;
    await api.buddyBlock(p.owner_key).catch(() => {});
    onBlocked?.();   // parent re-searches so the card disappears immediately
  }

  const chips = [p.time_window, p.vibe, p.skill_level, p.budget,
    p.group_size && `${p.group_size} people`].filter(Boolean);

  return (
    <div className="bud-card">
      <div className="bud-card-head">
        <strong>{p.display_name}</strong>
        <span className="bud-where">{[p.city, p.area].filter(Boolean).join(" · ")}</span>
      </div>
      <p className="bud-activity">{p.activity}</p>
      {chips.length > 0 && (
        <div className="bud-chips">{chips.map((c) => <span key={c}>{c}</span>)}</div>
      )}
      {p.notes && <p className="bud-notes">{p.notes}</p>}
      {p.match_reasons?.length > 0 && (
        <p className="bud-reasons">{p.match_reasons.join(" · ")}</p>
      )}
      <div className="bud-card-actions">
        {state === "sent" ? (
          <span className="bud-sent">Request sent ✓</span>
        ) : asking ? (
          <>
            <input placeholder="Your display name" value={name} maxLength={40}
              onChange={(e) => setName(e.target.value)} />
            <input placeholder="Short message (optional)" value={message} maxLength={280}
              onChange={(e) => setMessage(e.target.value)} />
            <button className="primary" onClick={sendRequest}>Send</button>
          </>
        ) : (
          <button className="primary" onClick={() => setAsking(true)}>Request to join</button>
        )}
        <button className="bud-minor" onClick={report}>Report</button>
        <button className="bud-minor" onClick={block}>Block</button>
      </div>
      {state && state !== "sent" && <p className="bud-note">{state}</p>}
    </div>
  );
}

function Browse() {
  const [filters, setFilters] = useState({ activity: "", city: "" });
  const [results, setResults] = useState(null);
  const [note, setNote] = useState(null);

  async function search() {
    setNote(null);
    try {
      const r = await api.buddySearch(searchQuery(filters));
      if (!r.ok) { setNote(`Search failed (${r.status})`); return; }
      setResults((await r.json()).posts ?? []);
    } catch {
      setNote("Can't reach the backend.");
    }
  }

  return (
    <div>
      <div className="bud-search-row">
        <input placeholder="Activity, like swimming" value={filters.activity}
          onChange={(e) => setFilters((f) => ({ ...f, activity: e.target.value }))}
          onKeyDown={(e) => e.key === "Enter" && search()} />
        <input placeholder="City" value={filters.city}
          onChange={(e) => setFilters((f) => ({ ...f, city: e.target.value }))}
          onKeyDown={(e) => e.key === "Enter" && search()} />
        <button className="primary" onClick={search}>Search</button>
      </div>
      <p className="bud-safety">{SAFETY_NOTE}</p>
      {note && <p className="bud-note">{note}</p>}
      {results?.length === 0 && (
        <p className="side-hint">No buddies match yet. Publish a post so
          they can find you instead.</p>
      )}
      {results?.map((post) => <MatchCard key={post.id} post={post} onBlocked={search} />)}
    </div>
  );
}

function Requests({ requests, onDecide }) {
  const incoming = requests?.incoming ?? [];
  const outgoing = requests?.outgoing ?? [];
  return (
    <div>
      <h4 className="bud-subhead">Incoming</h4>
      {incoming.length === 0 && <p className="side-hint">No requests on your posts yet.</p>}
      {incoming.map((q) => (
        <div className="bud-card" key={q.id}>
          <div className="bud-card-head">
            <strong>{q.requester_name || "A VITAL member"}</strong>
            <span className="bud-where">{q.activity}</span>
          </div>
          {q.message && <p className="bud-notes">“{q.message}”</p>}
          {q.status === "pending" ? (
            <div className="bud-card-actions">
              <button className="primary" onClick={() => onDecide(q.id, "accepted")}>Accept</button>
              <button className="bud-minor" onClick={() => onDecide(q.id, "rejected")}>Decline</button>
            </div>
          ) : (
            <p className="bud-sent">{q.status}</p>
          )}
        </div>
      ))}
      <h4 className="bud-subhead">Sent by you</h4>
      {outgoing.length === 0 && <p className="side-hint">You haven't requested to join anything yet.</p>}
      {outgoing.map((q) => (
        <div className="bud-card" key={q.id}>
          <div className="bud-card-head">
            <strong>{q.activity}</strong>
            <span className="bud-where">with {q.display_name}</span>
          </div>
          <p className={`bud-status bud-status-${q.status}`}>{q.status}</p>
        </div>
      ))}
    </div>
  );
}

export default function Buddies() {
  const [mine, setMine] = useState([]);
  const [requests, setRequests] = useState({ incoming: [], outgoing: [] });
  const [view, setView] = useState(null); // null | create | browse | requests

  const refresh = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([api.buddyMine(), api.buddyRequests()]);
      if (m.ok) setMine((await m.json()).posts ?? []);
      if (r.ok) setRequests(await r.json());
    } catch { /* board is optional; the rest of the panel still works */ }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // the dialog owns the screen while open; Esc closes it
  useEffect(() => {
    if (!view) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setView(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view]);

  async function toggleActive(post) {
    await api.buddyUpdate(post.id, { active: !post.active }).catch(() => {});
    refresh();
  }

  async function decide(id, status) {
    await api.buddyDecide(id, status).catch(() => {});
    refresh();
  }

  const pending = (requests.incoming ?? []).filter((q) => q.status === "pending").length;
  const [title, subtitle] = view ? HEADINGS[view] : ["", ""];

  return (
    <section className="card">
      <h3>Activity Buddies</h3>
      {mine.length === 0 ? (
        <p className="side-hint">Opt in to find people for shared activities.
          Others see only what you choose to post, under a display name you
          pick. Early feature, so expect a few rough edges.</p>
      ) : (
        mine.map((p) => (
          <div className="bud-mine-row" key={p.id}>
            <span className="bud-mine-what">{p.activity}
              {p.pending_requests > 0 && <em> · {p.pending_requests} pending</em>}
            </span>
            <button className={`bud-toggle ${p.active ? "on" : ""}`}
              title={p.active ? "Visible in search. Click to hide" : "Hidden. Click to show"}
              onClick={() => toggleActive(p)}>{p.active ? "active" : "paused"}</button>
          </div>
        ))
      )}
      <div className="bud-actions">
        <button onClick={() => setView("create")}>
          {mine.length ? "New post" : "Create a buddy post"}
        </button>
        <button onClick={() => setView("browse")}>Find buddies</button>
        <button onClick={() => setView("requests")}>
          Requests{pending > 0 ? ` (${pending})` : ""}
        </button>
      </div>

      {view && typeof document !== "undefined" && createPortal(
        <div className="bud-overlay" onClick={() => setView(null)}>
          <div className="bud-modal" role="dialog" aria-modal="true"
            aria-label={title} onClick={(e) => e.stopPropagation()}>
            <div className="bud-modal-head">
              <div className="bud-tabs" role="tablist">
                {TABS.map((t) => (
                  <button key={t.id} role="tab" aria-selected={view === t.id}
                    className={view === t.id ? "on" : ""}
                    onClick={() => setView(t.id)}>{t.label}</button>
                ))}
              </div>
              <button className="bud-close" aria-label="Close"
                onClick={() => setView(null)}>×</button>
            </div>
            <div className="bud-modal-body">
              <h4 className="bud-title">{title}</h4>
              <p className="bud-sub">{subtitle}</p>
              {view === "create" && (
                <CreateForm onCreated={() => { refresh(); setView(null); }} />
              )}
              {view === "browse" && <Browse />}
              {view === "requests" && <Requests requests={requests} onDecide={decide} />}
            </div>
          </div>
        </div>,
        document.body)}
    </section>
  );
}
