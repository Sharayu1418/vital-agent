# VITAL web

Next.js frontend for the VITAL backend (`../vital-app`).

## Local dev

```bash
# terminal 1: backend
cd ../vital-app && uv run uvicorn vital.api:app --app-dir src --reload

# terminal 2: frontend  (pnpm is the chosen package manager — pinned in package.json)
pnpm install
pnpm dev             # http://localhost:3000
```

Backend CORS already allows http://localhost:3000 (FRONTEND_ORIGIN default),
and SameSite=Lax cookies work because localhost:3000 → localhost:8000 is
same-site.

## Deploy — cookie reality check

The session cookie is `SameSite=Lax` by default, which does NOT survive a
cross-SITE hop like `your-app.vercel.app` → `your-api.run.app`. Two options:

**Option A (recommended): one site, two subdomains.**
Point `app.yourdomain.com` at Vercel and `api.yourdomain.com` at Cloud Run
(custom domains on both). Same registrable domain = same site = Lax cookies
just work, no CSRF surface added.

**Option B: cross-site with SameSite=None.**
On the backend set `SESSION_COOKIE_SAMESITE=none` (requires
`SESSION_COOKIE_SECURE=true`; the app refuses to boot otherwise). This
activates the origin-check CSRF guard: POST/DELETE requests with an Origin
header that isn't `FRONTEND_ORIGIN` get 403. Caveat: Safari/iOS is
increasingly hostile to third-party cookies — if you see login loops there,
that's your signal to move to Option A or token auth (Clerk/Firebase).

```bash
npx vercel
# Vercel env: NEXT_PUBLIC_API_BASE = https://api.yourdomain.com  (option A)
# Backend env: FRONTEND_ORIGIN = https://app.yourdomain.com
```

## What's in the UI

Chat with SSE streaming and per-tool status lines, plan cards with
Approve / Reject / edit-request (drives the backend interrupt), sleep-data
upload (Apple Health XML or CSV), "What VITAL knows" memory drawer with
per-fact delete, thumbs feedback on every AI response, and a daily-budget
notice when the 429 hits.
