import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const apiTarget = process.env.VITE_API_PROXY_TARGET || "http://localhost:8788";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发时 /api 转发到本地 FastAPI；/analyze-proxy 转发到本地简历诊断服务（:3000）
      "/api": { target: apiTarget, changeOrigin: true },
      "/analyze-proxy": {
        target: "http://localhost:3000",
        changeOrigin: true,
        rewrite: () => "/api/analyze",
      },
    },
  },
});
