import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // "export" is for production (Cloudflare Pages). Remove locally if needed,
  // but next dev ignores this setting so it's safe to leave.
  output: "export",
};

export default nextConfig;
