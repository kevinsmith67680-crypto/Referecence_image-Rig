import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI backend (`autorig editor <package>`) serves /api; in dev, Vite proxies to it.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 1200 },
  test: { environment: "node", globals: true, include: ["src/**/*.test.ts"] },
});
