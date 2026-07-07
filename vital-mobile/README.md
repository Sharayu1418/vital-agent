# VITAL mobile (Expo / React Native — Android)

Same FastAPI backend as web; nothing server-side is mobile-specific except
the `X-Vital-Session` header transport (cookies don't work in RN).

## Dev

```bash
pnpm install
pnpm start            # Expo dev server; press 'a' for Android emulator
```

API base resolution order: `EXPO_PUBLIC_API_BASE` env var → `app.json`
`expo.extra.apiBase` → `http://10.0.2.2:8000` default.
- Android **emulator**: the default works (10.0.2.2 = host localhost)
- **Physical phone** via Expo Go: pass your LAN IP as env so it never gets
  committed: `EXPO_PUBLIC_API_BASE=http://192.168.x.x:8000 pnpm start`
- Production build: set `EXPO_PUBLIC_API_BASE` to the Cloud Run URL in the
  EAS build profile (HTTPS)

## Known constraints

- Streaming uses `expo/fetch` (SDK 52+), which supports response-body
  streaming; plain RN `fetch` does not. If tokens don't stream, check the
  Expo SDK version first.
- Cleartext HTTP (dev against localhost) works in Expo Go; a production
  build only allows HTTPS — point it at Cloud Run.
- No Health Connect integration yet — that's the reason RN was chosen over
  a web wrapper; it lands post-launch via `react-native-health-connect`.

## Play Store path

1. Accounts: Google Play Console ($25 one-time), Expo account (free).
2. `npx eas init` then `eas build --platform android --profile production`
   → produces an `.aab` signed by EAS.
3. Play Console → create app → upload the `.aab` to **Internal testing**
   first (your friends = Phase 5 users; this also satisfies Play's
   pre-launch testing requirements for new personal accounts).
4. Data safety form: declare health data collection (sleep uploads) —
   Play takes this seriously; the honest answer is "collected, not shared".
   Link the memory-viewer as your in-app data control and add a privacy
   policy URL (a page in vital-web works).
5. Promote internal → closed → production when stable.

Note: because VITAL handles health data, review Play's Health apps policy
before submission — sleep data puts you in scope. LIMITATIONS.md's "not a
medical tool" language belongs in your store listing too.
