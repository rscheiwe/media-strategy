import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.{js,jsx}"],
    setupFiles: ["./src/test-setup.js"],
  },
});
