import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // ⚠️ Vite 不会读 tsconfig 的 paths，必须在这里再配一份，
    //    否则 tsc 通过、vite build 却报 "failed to resolve import @/App"。
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // ⚠️ 不写死端口：5173 可能落在 Windows 保留段（Hyper-V/WSL 动态保留，
    //    实测开加速器后 5141-5240 被保留）→ vite 会 EACCES 起不来。
    //    这里允许 --port 覆盖，且 strictPort=false 时自动往后找可用端口。
    port: Number(process.env.VITE_PORT ?? 5173),
    strictPort: false,
    strictPort: true,
    // 后端跑在 8000，前端直连会跨域。开发期用代理，
    // 生产部署时 nginx 反代同一个前缀即可，前端代码不用改。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
