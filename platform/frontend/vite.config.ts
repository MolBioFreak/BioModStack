import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'url'
import { dirname, resolve } from 'path'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      // Point to the bms-plugin dist folder for ESM bundle
      '@biomodstack/bms-plugin': resolve(__dirname, '../../../../MolBio-Open-Source-Toolkits/apps/bms-plugin/dist/index.mjs'),
      '@biomodstack/bms-plugin/dist/bms-plugin.css': resolve(__dirname, '../../../../MolBio-Open-Source-Toolkits/apps/bms-plugin/dist/bms-plugin.css'),
    }
  },
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

