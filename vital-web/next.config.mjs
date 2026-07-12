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
