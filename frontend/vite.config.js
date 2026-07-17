import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    allowedHosts: ['likewise-eloquent-protegee.ngrok-free.dev'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5002',
        changeOrigin: true
      }
    }
  }
})
