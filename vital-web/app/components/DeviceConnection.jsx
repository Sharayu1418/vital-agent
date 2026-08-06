"use client";
import { useState } from "react";
import { api } from "../lib/api";

/* Wearable connection status.
 *
 * The "Reconnect" state is the reason this component is not just a button.
 * While the Google Cloud project is in Testing publishing status, refresh
 * tokens expire after 7 days — so the connection WILL break on a schedule,
 * by design, and a broken sync that shows nothing looks exactly like a
 * quiet night's sleep. It has to be visible.
 *
 * The disclosure line is not decoration either. Google requires apps using
 * restricted health scopes to state, inside the app and in normal use,
 * what data is collected and why — explicitly not buried in a settings
 * menu or a privacy policy.
 */

function ago(iso) {
  if (!iso) return null;
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function DeviceConnection({ connection, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  if (!connection?.available) return null;

  const { label, connected, last_sync_at: lastSync, needs_reconnect: needsReconnect } = connection;

  async function connect() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.connectStart(connection.provider);
      const body = await res.json();
      if (!res.ok || !body.authorize_url) {
        setNote(body.detail || "Could not start the connection.");
        return;
      }
      // Full navigation, not a popup: the consent screen is Google's and
      // blocks framing, and popups get suppressed on mobile Safari.
      window.location.href = body.authorize_url;
    } catch {
      setNote("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function syncNow() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.connectSync(connection.provider);
      const body = await res.json();
      setNote(body.error
        ? body.error
        : `Synced ${body.synced ?? 0} night${body.synced === 1 ? "" : "s"}.`);
      onChanged?.();
    } catch {
      setNote("Sync failed.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    // States plainly what is being erased. "Are you sure?" would not.
    const ok = window.confirm(
      `Disconnect ${label}?\n\nThis revokes VITAL's access and deletes every `
      + `night synced from it. Sleep you logged by hand, and anything you `
      + `uploaded yourself, are kept.`);
    if (!ok) return;
    setBusy(true);
    try {
      const res = await api.connectDisconnect(connection.provider);
      const body = await res.json();
      setNote(body.deleted_nights != null
        ? `Disconnected. Removed ${body.deleted_nights} synced nights.`
        : "Disconnected.");
      onChanged?.();
    } catch {
      setNote("Could not disconnect.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sidebar-device" aria-labelledby="device-title">
      <div className="sidebar-memory-head">
        <h2 id="device-title">Devices</h2>
        {connected && !needsReconnect && lastSync && (
          <span className="device-sync" title={new Date(lastSync).toLocaleString()}>
            {ago(lastSync)}
          </span>
        )}
      </div>

      {!connected ? (
        <>
          <p className="side-hint">
            {label} collects your sleep timing and duration to personalise your
            energy forecast and plans. Nothing is shared with anyone else.
          </p>
          <button className="device-btn" onClick={connect} disabled={busy}>
            {busy ? "Opening…" : `Connect ${label}`}
          </button>
        </>
      ) : needsReconnect ? (
        <>
          <p className="side-hint device-warn" role="status">
            {label} needs reconnecting — the authorisation expired, so new
            nights aren’t syncing.
          </p>
          <button className="device-btn warn" onClick={connect} disabled={busy}>
            Reconnect
          </button>
          <button className="device-link" onClick={disconnect} disabled={busy}>
            Remove
          </button>
        </>
      ) : (
        <>
          <p className="side-hint">
            {label} connected{lastSync ? "" : " — not synced yet"}.
          </p>
          <button className="device-btn" onClick={syncNow} disabled={busy}>
            {busy ? "Syncing…" : "Sync now"}
          </button>
          <button className="device-link" onClick={disconnect} disabled={busy}>
            Disconnect
          </button>
        </>
      )}

      {note && <p className="side-hint" role="status">{note}</p>}
    </section>
  );
}
