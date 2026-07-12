# Firebase Google Sign-In — one-time setup

The code is already wired end to end: the web app signs in with Google via
Firebase, sends the ID token as `Authorization: Bearer <token>`, and the
backend verifies it with the Firebase Admin SDK and maps the Firebase UID to
a stable internal VITAL identity (`auth_identities` table in Postgres).
What remains is console configuration — about 10 minutes, no code.

## 1. Firebase Console

1. Go to <https://console.firebase.google.com> and **Add project** →
   choose the **existing** GCP project `vital-agent-dev` (this attaches
   Firebase to the project; it does not create a new one).
2. **Build → Authentication → Get started.**
3. **Sign-in method** tab → enable **Google** as a provider (pick the
   support email when prompted).
4. **Settings → Authorized domains**: make sure `localhost` is present and
   add `vital-agent.vercel.app`.
5. **Project settings (gear) → Your apps → Add app → Web (`</>`)**.
   Name it `vital-web`. Skip Hosting. Copy the config values shown
   (apiKey, authDomain, projectId, appId).

## 2. Vercel (frontend)

Project → Settings → Environment Variables, add for Production (and
Preview if you use it):

```
NEXT_PUBLIC_FIREBASE_API_KEY=<from step 1.5>
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=<project>.firebaseapp.com
NEXT_PUBLIC_FIREBASE_PROJECT_ID=vital-agent-dev
NEXT_PUBLIC_FIREBASE_APP_ID=<from step 1.5>
```

These are public web configuration, not secrets — they ship in the JS
bundle by design. Real access control is the backend's token verification.
Redeploy the frontend after saving.

With these set, the web app is **OAuth-first**: it shows a login screen and
renders nothing else — no chat, panels, buddy board, upload, or threads —
until the user signs in with Google. Do **not** set `NEXT_PUBLIC_ALLOW_ANON`
in production; that flag exists only to run the app locally without Firebase
config (see `vital-web/.env.example`).

## 3. Cloud Run (backend)

Add these environment variables to the service and redeploy:

```
FIREBASE_AUTH_ENABLED=true
FIREBASE_PROJECT_ID=vital-agent-dev
AUTH_REQUIRED=true
```

`AUTH_REQUIRED=true` makes VITAL OAuth-first: every user-data route
(`/chat`, `/upload/health`, `/sleep/recent`, `/calendar`, `/memories`,
`/feedback`, `/threads/*`, and the whole buddy board) returns 401 for
unauthenticated callers instead of minting an anonymous session. Public
routes (`/healthz`, `/docs`, `/openapi.json`) stay open. Startup refuses
to boot if `AUTH_REQUIRED=true` with no authenticator configured, so you
can't accidentally ship a wall of dead 401s.

The Admin SDK authenticates with **Application Default
Credentials** — on Cloud Run that's the service's own identity. Do **not**
create or mount a service-account JSON key; none is needed, and the
backend refuses to use one because the code never reads one. If Firebase
init fails (wrong project id, missing IAM), the service fails at startup
rather than serving broken auth.

Local backend dev works the same way via
`gcloud auth application-default login`, but you rarely need it: leave
`FIREBASE_AUTH_ENABLED=false` locally and everything runs anonymously;
tests mock verification and never touch the network.

## 4. What signing in does

- First sign-in from a browser that already has anonymous VITAL data
  links that data to the Google account (server-side, atomic).
- The same Google account then resolves to the same VITAL identity on any
  device or browser.
- Signing out expires the anonymous session server-side; an old session
  that was linked can never read account data again. The account's data
  is never deleted — sign back in to see it.

## Known limits

- Thread titles created **before** sign-in on a *different* device can't
  be imported into the account's thread list — there is no way to prove
  ownership of a bare localStorage thread id, so we deliberately don't
  claim them. The device that created them still shows them locally.
- Google **Calendar** access is a separate, future OAuth consent flow
  (different scopes, different tokens). Firebase sign-in here only proves
  identity.
