import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@opentelemetry/api": path.resolve(__dirname, "src/lib/otel-api-shim.ts"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 3001,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000/chat",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
