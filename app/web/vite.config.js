import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Dev server: the API runs separately on :7666 and is proxied, so the app always
// calls same-origin /app/* (as it does behind nginx in docker).
const API_URL = process.env.VITE_DEV_API_URL || "http://localhost:7666";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5666,
    proxy: {
      "/app": { target: API_URL, changeOrigin: true },
      "/health": { target: API_URL, changeOrigin: true },
    },
  },
});
