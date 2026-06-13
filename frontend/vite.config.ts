import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // Windows + Docker bind mount: inotify 事件传不进容器, Vite 默认监听收不到改动 →
    // 热更新静默失效, 改了 .tsx 浏览器不刷新。开 usePolling 用轮询兜底, 强制能感知改动。
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/api': {
        target: process.env.VITE_API_BASE_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
