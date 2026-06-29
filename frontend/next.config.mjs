/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export served by the FastAPI backend under /app (one service).
  output: 'export',
  basePath: '/app',
  assetPrefix: '/app',
  trailingSlash: true,
  images: { unoptimized: true },
  // The app is a client-side SPA hitting the FastAPI REST + /ws/stream API.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || '',
  },
};

export default nextConfig;
