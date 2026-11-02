import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 构建产物放进 Python 包，由 tunetidy ui 提供；不提交进仓库
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/tunetidy/server/static",
    emptyOutDir: true,
  },
  server: {
    // 开发时：先另开终端运行 tunetidy ui --no-browser，再用它打印的口令打开
    // http://localhost:5173/#token=...。服务端只接受本机 Host 和同源 Origin，
    // 所以代理把两者改成服务端自己的地址（只影响本机开发；口令照样要带）。
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        headers: { origin: "http://127.0.0.1:8765" },
      },
    },
  },
});
