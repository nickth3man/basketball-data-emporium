import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import checker from "vite-plugin-checker";
import { visualizer } from "rollup-plugin-visualizer";

// Resolve from this config file so the alias is path-stable regardless of
// the cwd (Vite's `resolve.alias` requires an absolute path on Windows).
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    checker({
      typescript: { tsconfigPath: "tsconfig.app.json" },
      eslint: {
        useFlatConfig: true,
        lintCommand: "eslint ./src",
      },
      overlay: { initialIsOpen: false },
    }),
    ...(process.env.ANALYZE
      ? [
          visualizer({
            open: true,
            filename: "dist/stats.html",
            gzipSize: true,
            brotliSize: true,
          }),
        ]
      : []),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8787",
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (req) => {
            if (req.path?.includes("/chat/stream")) {
              req.setHeader("Connection", "keep-alive");
            }
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
