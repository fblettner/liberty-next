// Vitest baseline (A7.2). Tests live next to the module they cover (`*.test.ts(x)`), run in
// jsdom so modules that touch `localStorage` / `fetch` at import time (api/client.ts) load.
// Start with the pure modules (action runner, DML value parser, id validation, cell rules);
// component tests (React Testing Library) can be added on the same runner.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    globals: false,
  },
});
