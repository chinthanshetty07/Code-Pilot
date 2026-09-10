import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Pure-logic unit tests only (no component rendering) -- no jsdom
// environment or React plugin needed, keeping this fast and dependency-light.
// The `@/*` alias mirrors tsconfig.json's own `paths` entry, which Vitest
// doesn't read automatically.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next"],
  },
});
