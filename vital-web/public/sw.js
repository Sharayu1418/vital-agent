/* Service worker — exists solely to receive the morning brief.
 *
 * No caching, no offline shell, no fetch handler. A service worker that
 * intercepts requests is a whole class of "why is the app showing me
 * yesterday's data" bugs, and none of that is needed to show a
 * notification. This does one job.
 */

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }

  // Never show an empty notification. If the payload did not arrive
  // intact, staying silent is better than a blank card on a lock screen.
  if (!payload.title && !payload.body) return;

  event.waitUntil(
    self.registration.showNotification(payload.title || "VITAL", {
      body: payload.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      /* One brief a day, and a replacement should REPLACE. Without a tag,
         a retry stacks a second card. */
      tag: "vital-morning-brief",
      renotify: false,
      data: { url: payload.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin);

  /* Focus an existing tab rather than opening a fifth one. Someone who
     already has VITAL open does not want another copy of it. */
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        for (const client of clients) {
          if (new URL(client.url).origin === target.origin && "focus" in client) {
            client.navigate(target.href);
            return client.focus();
          }
        }
        return self.clients.openWindow(target.href);
      })
  );
});

/* Push services rotate subscriptions without warning. Without this the
   subscription silently dies and the brief stops arriving with nothing to
   see — the failure mode this whole feature is most vulnerable to. */
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    self.registration.pushManager
      .subscribe(event.oldSubscription?.options)
      .then((subscription) =>
        fetch("/brief/resubscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(subscription),
        }).catch(() => {})
      )
      .catch(() => {})
  );
});
