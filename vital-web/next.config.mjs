/* P1-11: the localhost fallback below is right for local dev and wrong
 * everywhere else. If NEXT_PUBLIC_API_BASE is missing, misspelled, or scoped
 * to the wrong Vercel environment, the build used to SUCCEED and ship a
 * bundle pointing at localhost:8000 — every request then failed with a
 * generic "Can't reach the backend", with no signal to us.
 *
 * VERCEL_ENV is set on every Vercel build (production | preview |
 * development), so its presence means "this is a deploy, not a laptop".
 * Preview is included deliberately: a preview aimed at localhost is exactly
 * the confusing half-broken deploy this is meant to prevent. */
if (process.env.VERCEL_ENV && !process.env.NEXT_PUBLIC_API_BASE) {
  throw new Error(
    `NEXT_PUBLIC_API_BASE is not set for VERCEL_ENV=${process.env.VERCEL_ENV}. ` +
    "Refusing to build a bundle that would silently point at localhost:8000. " +
    "Set it in Vercel → Settings → Environment Variables for this environment.",
  );
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    // FastAPI backend. Local dev default; set on Vercel to the Cloud Run URL.
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
  },
  async rewrites() {
    return [
      {
        source: "/__/auth/:path*",
        destination: "https://vital-agent-dev.firebaseapp.com/__/auth/:path*",
      },
    ];
  },
};
export default nextConfig;
