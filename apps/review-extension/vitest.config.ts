import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    maxWorkers: 4,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{mjs,ts}"],
    environment: "node",
    restoreMocks: true,
    unstubGlobals: true,
  },
});
