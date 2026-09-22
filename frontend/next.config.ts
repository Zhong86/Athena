import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone with only the traced files, so the runtime image
  // ships without node_modules. Note the build still has to copy `public` and
  // `.next/static` in by hand -- standalone/server.js does not include them.
  output: "standalone",
};

export default nextConfig;
