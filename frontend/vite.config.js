import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发时 /api 转发到本地 FastAPI；/analyze-proxy 转发到本地简历诊断服务（:3000）
      "/api": { target: "http://localhost:8788", changeOrigin: true },
      "/analyze-proxy": {
        target: "http://localhost:3000",
        changeOrigin: true,
        rewrite: () => "/api/analyze",
      },
    },
  },
});
