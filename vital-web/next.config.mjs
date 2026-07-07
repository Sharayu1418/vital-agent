/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    // FastAPI backend. Local dev default; set on Vercel to the Cloud Run URL.
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000",
  },
};
export default nextConfig;
