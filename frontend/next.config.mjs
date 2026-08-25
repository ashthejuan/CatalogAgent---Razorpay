/** @type {import('next').NextConfig} */
// Proxy /api/* to the CatalogAgent backend (FastAPI on :8000) so the browser
// talks same-origin (no CORS preflight) and the X-Buyer-Key header is allowed.
const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/:path*` },
    ];
  },
};

export default nextConfig;
