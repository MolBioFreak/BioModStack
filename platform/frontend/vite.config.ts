import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import path from 'path'


// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  // Use /bms/ for production (Tailscale Serve proxy), but / for dev mode
  base: mode === 'production' ? '/bms/' : '/',
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
    // Tailscale Serve proxies https://compute-node.taileb3a90.ts.net → 127.0.0.1:5173
    // HMR disabled: WebSocket can't negotiate wss↔ws through the TLS proxy,
    // causing remote clients to hang.  Local edits still trigger a full reload.
    hmr: false,
    allowedHosts: ['compute-node.taileb3a90.ts.net'],
    // Prevent watching pipeline directories that can have millions of files
    watch: {
      ignored: [
        '**/work/**',
        '**/bms_results/**',
        '**/models/**',
        '**/apptainer/**',
        '**/binderscaffolds/**',
      ]
    },
    proxy: {
      // MJPEG stream: dedicated entry to prevent http-proxy from buffering
      // the multipart/x-mixed-replace response (must precede generic /api)
      '/api/bioxp/camera/mjpeg': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes: any) => {
            proxyRes.headers['x-accel-buffering'] = 'no';
            proxyRes.headers['cache-control'] = 'no-cache, no-store, no-transform';
          });
        },
      },
      // Proxy /api requests to backend server
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
}))

