import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built `dist/` is served as static by the FastAPI backend (liberty/main.py).
// In dev (`npm run dev`), proxy the API paths to a backend on :8000.
const backend = "http://127.0.0.1:8000";
const proxied = ["/api", "/auth", "/ai", "/admin", "/health", "/info", "/docs", "/openapi.json", "/manifest.webmanifest"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxied.map((p) => [p, { target: backend, changeOrigin: true }])),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Vendor split (same groups as nomaubl): the heavy libraries get their own long-cached
    // chunks instead of riding whichever page chunk imports them first. Monaco is loaded only
    // by the SQL editor (lazy — see common/SchemaForm.tsx); the two locale files leave the
    // entry too. `chunkSizeWarningLimit` is set so the warning stays honest for real regressions.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vite's virtual helper modules (the `__vitePreload` dynamic-import helper, the
          // CommonJS shims) are imported by nearly every chunk. Left to Rollup they land in
          // whichever manual chunk is processed first — on the first attempt that was
          // `monaco`, which made the 2.3 MB editor a STATIC dependency of the entry again.
          // Pin them to the always-loaded vendor chunk.
          if (id.startsWith("\0") || id.includes("vite/preload-helper") || id.includes("commonjsHelpers")) return "react";
          if (id.includes("node_modules/monaco-editor") || id.includes("node_modules/@monaco-editor")) return "monaco";
          if (id.includes("node_modules/recharts") || id.includes("node_modules/d3-")) return "recharts";
          if (id.includes("node_modules/xlsx")) return "xlsx";
          if (id.includes("node_modules/@tanstack")) return "table";
          if (id.includes("node_modules/socket.io") || id.includes("node_modules/engine.io")) return "socket";
          if (id.includes("node_modules/react-markdown") || id.includes("node_modules/remark") ||
              id.includes("node_modules/micromark") || id.includes("node_modules/mdast") ||
              id.includes("node_modules/unified") || id.includes("node_modules/hast")) return "markdown";
          if (id.includes("node_modules/react") || id.includes("node_modules/scheduler") ||
              id.includes("node_modules/@emotion") || id.includes("node_modules/i18next") ||
              id.includes("node_modules/react-i18next")) return "react";
          if (id.includes("/src/locales/")) return "locales";
          return undefined;
        },
      },
    },
  },
});
