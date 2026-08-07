/* Browser side of the morning brief.
 *
 * The permission prompt is the delicate part. Browsers permanently block a
 * site that asks and gets denied — there is no second chance and no way to
 * re-ask. So nothing here runs on page load: the prompt appears only after
 * someone has explicitly turned the brief on, by which point they know what
 * they are agreeing to.
 */

export function pushSupported() {
  return typeof window !== "undefined"
    && "serviceWorker" in navigator
    && "PushManager" in window
    && "Notification" in window;
}

/* iOS Safari only delivers push to an installed PWA. Worth detecting so the
 * UI can say "add to home screen first" instead of silently never working —
 * which is exactly the kind of quiet failure this app keeps being bitten
 * by. */
export function needsHomeScreenInstall() {
  if (typeof window === "undefined") return false;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const installed = window.matchMedia?.("(display-mode: standalone)")?.matches
    || window.navigator.standalone === true;
  return ios && !installed;
}

/* VAPID keys travel as base64url; PushManager wants a Uint8Array. */
export function urlBase64ToUint8Array(base64) {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(padded);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export async function enablePush(vapidPublicKey) {
  if (!pushSupported()) throw new Error("This browser can't show notifications.");
  if (!vapidPublicKey) throw new Error("Notifications aren't configured on the server.");

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error(
      permission === "denied"
        ? "Notifications are blocked for this site — you'll need to allow them in your browser settings."
        : "Notifications weren't allowed.");
  }

  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;

  // Reuse an existing subscription rather than creating a second one for
  // the same device.
  const existing = await registration.pushManager.getSubscription();
  const subscription = existing ?? await registration.pushManager.subscribe({
    userVisibleOnly: true,      // required; we only ever show visible notifications
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
  });

  const json = subscription.toJSON();
  return {
    endpoint: json.endpoint,
    p256dh: json.keys?.p256dh,
    auth: json.keys?.auth,
  };
}

export async function currentEndpoint() {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = await registration?.pushManager?.getSubscription();
  return subscription?.endpoint ?? null;
}

export async function disablePush() {
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = await registration?.pushManager?.getSubscription();
  const endpoint = subscription?.endpoint ?? null;
  await subscription?.unsubscribe();
  return endpoint;
}
