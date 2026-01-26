import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import path from 'path'


// https://vite.dev/config/
export default defineConfig({
  base: '/bms/',  // Required for Tailscale Serve proxy at /bms
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    include: [
      '@blueprintjs/icons',
      '@blueprintjs/icons/lib/esm/paths/16px',
      '@blueprintjs/icons/lib/esm/paths/20px',
      'buffer',
      'string_decoder',
      'events',
      'plotly.js-dist-min',
      'react-plotly.js',
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
    host: '0.0.0.0',
    allowedHosts: ['compute-node.taileb3a90.ts.net'],
    // Prevent watching pipeline directories that can have millions of files
    watch: {
      ignored: [
        '**/work/**',
        '**/pdj_results/**',
        '**/models/**',
        '**/apptainer/**',
        '**/binderscaffolds/**',
      ]
    },
    proxy: {
      // Proxy /api requests to backend server
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})

