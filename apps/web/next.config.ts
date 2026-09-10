import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // Not using `output: "standalone"` for the production Docker image
  // (apps/web/Dockerfile.prod) -- it would produce a smaller final image,
  // but needs extra handling for a pnpm-workspace dependency
  // (@codepilot/shared-types) via `outputFileTracingRoot`, and the
  // simpler "copy the whole built repo" approach Dockerfile.prod uses now
  // is more predictable for a first deployment. Worth revisiting if image
  // size/build time ever actually becomes a problem.
};

export default nextConfig;
