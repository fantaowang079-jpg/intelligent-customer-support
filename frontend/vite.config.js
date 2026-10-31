import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 前端 dev 服务（5173）通过代理访问 8000 后端，规避跨域问题
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
