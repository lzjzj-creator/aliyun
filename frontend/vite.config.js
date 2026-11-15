import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发时 /api 转发到本地 FastAPI；简历分析直连部署的 analyze 服务地址在 api.js 里配
      "/api": { target: "http://localhost:8788", changeOrigin: true },
    },
  },
});
