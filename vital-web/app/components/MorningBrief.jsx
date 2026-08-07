"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import {
  currentEndpoint, disablePush, enablePush, needsHomeScreenInstall, pushSupported,
} from "../lib/push";

/* Morning brief opt-in.
 *
 * Two deliberate choices:
 *
 * 1. **Preview before permission.** You can see exactly what today's brief
 *    would say before agreeing to receive one at 7am. Browsers permanently
 *    block a site whose permission prompt is denied — there is no second
 *    chance — so the prompt only appears after someone has decided they
 *    want this.
 *
 * 2. **Off by default, and obvious how to leave.** A daily notification has
 *    one chance to be welcome. The unsubscribe is a plain control, not
 *    buried.
 */
export default function MorningBrief() {
  const [settings, setSettings] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await api.briefSettings();
      if (!res.ok) return;
      const body = await res.json();
      setSettings(body);

      /* Self-heal a rotated subscription. Push services replace
       * subscriptions without warning, and the service worker cannot tell
       * the server itself — it is served from a different origin than the
       * API. Re-registering here is an upsert keyed on the endpoint, so it
       * costs nothing when nothing has changed and quietly repairs the
       * notification path when it has. Without it the brief stops arriving
       * with no error anywhere. */
      if (body.enabled) {
        const registration = await navigator.serviceWorker?.getRegistration();
        const subscription = await registration?.pushManager?.getSubscription();
        if (subscription) {
          const json = subscription.toJSON();
          await api.briefSubscribe({
            endpoint: json.endpoint,
            p256dh: json.keys?.p256dh,
            auth: json.keys?.auth,
          }).catch(() => {});
        }
      }
    } catch { /* panel is decorative; chat still works */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!settings?.available) return null;

  const supported = pushSupported();
  const needsInstall = needsHomeScreenInstall();

  async function showPreview() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.briefPreview();
      const body = await res.json();
      setPreview(body.brief ? body.brief : { empty: body.reason });
    } catch {
      setNote("Couldn't build a preview.");
    } finally {
      setBusy(false);
    }
  }

  async function turnOn() {
    setBusy(true);
    setNote(null);
    try {
      const subscription = await enablePush(settings.vapid_public_key);
      const sub = await api.briefSubscribe(subscription);
      if (!sub.ok) throw new Error("Couldn't register this device.");
      await api.saveBriefSettings({ enabled: true, hour: settings.hour ?? 7 });
      setNote("On. You'll get one just after you usually wake up.");
      await load();
    } catch (err) {
      setNote(err.message || "Couldn't turn notifications on.");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff() {
    setBusy(true);
    setNote(null);
    try {
      const endpoint = await disablePush().catch(() => currentEndpoint());
      if (endpoint) await api.briefUnsubscribe(endpoint);
      await api.saveBriefSettings({ enabled: false, hour: settings.hour ?? 7 });
      setNote("Off. Nothing will be sent.");
      await load();
    } catch {
      setNote("Couldn't turn it off — try again.");
    } finally {
      setBusy(false);
    }
  }

  /* Delivery check. The hour is restricted to mornings, so without this
   * the only way to know notifications ARRIVE is to wait until tomorrow —
   * and delivery is the part most likely to be broken, crossing the
   * browser, the push service and VAPID signing. */
  async function sendTest() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.briefTest();
      const body = await res.json();
      if (!res.ok) {
        setNote(body.detail || "Couldn't send.");
        return;
      }
      setNote(body.sent
        ? (body.was_real_brief
            ? "Sent — that's today's real brief."
            : "Sent. Nothing to report today, so that was a delivery test.")
        : "No device accepted it. Try turning the brief off and on again.");
    } catch {
      setNote("Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  }

  async function changeHour(hour) {
    setSettings((s) => ({ ...s, hour }));
    await api.saveBriefSettings({ enabled: settings.enabled, hour }).catch(() => {});
  }

  return (
    <section className="sidebar-brief" aria-labelledby="brief-title">
      <div className="sidebar-memory-head">
        <h2 id="brief-title">Morning brief</h2>
        {settings.enabled && <span className="device-sync">on</span>}
      </div>

      {!settings.enabled ? (
        <p className="side-hint">
          One notification a day: how you slept, when you’ll be sharpest, and
          one thing worth changing. Nothing else.
        </p>
      ) : (
        <p className="side-hint">
          Arriving at {String(settings.hour).padStart(2, "0")}:00, on{" "}
          {settings.devices} {settings.devices === 1 ? "device" : "devices"}.
        </p>
      )}

      {preview && (
        <div className="brief-preview">
          {preview.empty ? (
            <p className="side-hint">{preview.empty}</p>
          ) : (
            <>
              <strong>{preview.title}</strong>
              <p>{preview.body}</p>
            </>
          )}
        </div>
      )}

      <button className="device-btn" onClick={showPreview} disabled={busy}>
        {busy ? "…" : "Show today’s brief"}
      </button>

      {!supported ? (
        <p className="side-hint">This browser can’t show notifications.</p>
      ) : needsInstall ? (
        <p className="side-hint">
          On iPhone, add VITAL to your home screen first — Safari only
          delivers notifications to installed apps.
        </p>
      ) : settings.enabled ? (
        <>
          <label className="brief-hour">
            Send at
            <select value={settings.hour}
              onChange={(e) => changeHour(Number(e.target.value))}>
              {[5, 6, 7, 8, 9, 10].map((h) => (
                <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
              ))}
            </select>
          </label>
          <button className="device-btn" onClick={sendTest} disabled={busy}>
            {busy ? "…" : "Send one now"}
          </button>
          <button className="device-link" onClick={turnOff} disabled={busy}>
            Turn off
          </button>
        </>
      ) : (
        <button className="device-btn" onClick={turnOn} disabled={busy}>
          {busy ? "…" : "Send it each morning"}
        </button>
      )}

      {note && <p className="side-hint" role="status">{note}</p>}
    </section>
  );
}
