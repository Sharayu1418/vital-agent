/* Client-only Firebase bootstrap.
 *
 * The NEXT_PUBLIC_FIREBASE_* values are public web configuration (they end
 * up in the served bundle by design) — real access control happens on the
 * backend, which verifies ID tokens with the Admin SDK. No secrets here,
 * and never any service-account credentials.
 *
 * SSR-safe: every entry point returns null when `window` is missing or the
 * config is absent, so static rendering and anonymous-only deployments
 * work without Firebase at all. The SDK is imported lazily to keep it out
 * of the server bundle. */

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

export function firebaseConfigured() {
  return Boolean(config.apiKey && config.authDomain && config.projectId && config.appId);
}

let authPromise = null;

/* The Firebase Auth instance, or null (SSR / unconfigured). Memoized so the
 * app initializes exactly once. */
export function getFirebaseAuth() {
  if (typeof window === "undefined" || !firebaseConfigured()) {
    return Promise.resolve(null);
  }
  if (!authPromise) {
    authPromise = (async () => {
      const { initializeApp, getApps } = await import("firebase/app");
      const { browserLocalPersistence, getAuth, setPersistence } =
        await import("firebase/auth");
      const app = getApps()[0] ?? initializeApp(config);
      const auth = getAuth(app);
      // survive browser restarts; the SDK owns token storage and refresh —
      // we never copy ID/refresh tokens into localStorage ourselves
      await setPersistence(auth, browserLocalPersistence).catch(() => {});
      return auth;
    })();
  }
  return authPromise;
}
