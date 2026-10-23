import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, import.meta.dirname, "CONFIGURATION_");
  const target =
    process.env.CONFIGURATION_API_URL ?? env.CONFIGURATION_API_URL ?? "http://127.0.0.1:26102";
  const proxy = {
    "/api": { target, changeOrigin: true },
    "/health": { target, changeOrigin: true },
  };
  return { plugins: [vue()], server: { proxy }, preview: { proxy } };
});
