import type { NextConfig } from "next";

// ponytail: in `next dev` proxy /api to a local uvicorn; on Vercel, vercel.json routes /api/* to api/index.py.
const nextConfig: NextConfig = {
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const port = process.env.PY_DEV_PORT ?? "8000";
    return [{ source: "/api/:path*", destination: `http://127.0.0.1:${port}/api/:path*` }];
  },
};

export default nextConfig;
