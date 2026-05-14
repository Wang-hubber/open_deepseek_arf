import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': `http://localhost:${process.env.VITE_BACKEND_PORT || '8000'}`,
      '/ws': {
        target: `ws://localhost:${process.env.VITE_BACKEND_PORT || '8000'}`,
        ws: true,
      },
    },
  },
})
