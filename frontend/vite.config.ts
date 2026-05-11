import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built `dist/` is served as static by the FastAPI backend (liberty/main.py).
// In dev (`npm run dev`), proxy the API paths to a backend on :8000.
const backend = "http://127.0.0.1:8000";
const proxied = ["/api", "/auth", "/ai", "/admin", "/health", "/info", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxied.map((p) => [p, { target: backend, changeOrigin: true }])),
  },
  build: { outDir: "dist", emptyOutDir: true },
});
