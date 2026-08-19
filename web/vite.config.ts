import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands directly in the Python package, so `flaky serve` works from a
// pip install with no Node toolchain present. The compiled assets are committed
// for the same reason; see docs/dashboard.md.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/flaky_detective/web/static",
    emptyOutDir: true,
    sourcemap: false,
    // A handful of larger chunks beats dozens of round trips for a local server.
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      // `npm run dev` talks to a `flaky serve` running alongside it.
      "/api": "http://127.0.0.1:8420",
    },
  },
});
