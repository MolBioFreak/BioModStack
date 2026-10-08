import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import path from 'path'


// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    include: [
      '@blueprintjs/icons',
      '@blueprintjs/icons/lib/esm/paths/16px',
      '@blueprintjs/icons/lib/esm/paths/20px',
    ],
    esbuildOptions: {
      loader: {
        '.js': 'jsx',
      },
    },
  },

  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      buffer: path.resolve(__dirname, 'node_modules/buffer/index.js'),
      'buffer/': path.resolve(__dirname, 'node_modules/buffer'),
      string_decoder: path.resolve(__dirname, 'node_modules/string_decoder/lib/string_decoder.js'),
      'string_decoder/': path.resolve(__dirname, 'node_modules/string_decoder'),
      events: path.resolve(__dirname, 'node_modules/events/events.js'),
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

