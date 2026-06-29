/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export served at the site root by the FastAPI backend (one service).
  output: 'export',
  trailingSlash: true,
  images: { unoptimized: true },
  // The app is a client-side SPA hitting the FastAPI REST + /ws/stream API.
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || '',
  },
};

export default nextConfig;
