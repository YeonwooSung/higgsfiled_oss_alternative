import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@oc/ui", "@oc/contracts", "@oc/web-core"],
  async rewrites() {
    // Same-origin API keeps the anonymous session cookie first-party and HttpOnly.
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: [{ key: "Referrer-Policy", value: "same-origin" }, { key: "X-Content-Type-Options", value: "nosniff" }] }];
  },
};

export default config;
