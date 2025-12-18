import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    allowedHosts: ['compute-node.taileb3a90.ts.net'],
    proxy: {
      // Proxy /api requests to backend server
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})

