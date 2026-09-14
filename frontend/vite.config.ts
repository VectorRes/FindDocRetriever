/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    globals: true,
    env: {
      VITE_API_URL: "http://localhost:8000",
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // Allow access via the docker-compose service name ("frontend"), not just
    // localhost — needed when another container (or a Playwright smoke test)
    // reaches this dev server through the compose network.
    allowedHosts: true,
    // Docker Desktop on Windows doesn't forward inotify events for bind
    // mounts, so chokidar never sees file changes without polling (same
    // issue the API hit with uvicorn --reload/watchfiles).
    watch: {
      usePolling: true,
    },
  },
});
