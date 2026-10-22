// Guest-only build for the public read-only share server.
// Output: dist-share/share.html + hashed assets, served from the site root.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import path from 'path'

const utilShimPath = path.resolve(__dirname, 'src/shims/util.ts')

export default defineConfig({
  base: '/',
  // Never copy the main app's public/ tree (it holds unrelated fixtures) onto the public server.
  publicDir: false,
  plugins: [react(), tailwindcss()],
  // The guest page has no BMS build identity, no API proxy and no dev server.
  define: {
    __BMS_BUILD_METADATA__: JSON.stringify({ layer: 'share', revision: 'unknown', buildId: 'share', buildTime: 'unknown' }),
  },
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      buffer: path.resolve(__dirname, 'node_modules/buffer/index.js'),
      'buffer/': path.resolve(__dirname, 'node_modules/buffer'),
      'safe-buffer': path.resolve(__dirname, 'node_modules/safe-buffer/index.js'),
      string_decoder: path.resolve(__dirname, 'node_modules/string_decoder/lib/string_decoder.js'),
      'string_decoder/': path.resolve(__dirname, 'node_modules/string_decoder'),
      events: path.resolve(__dirname, 'node_modules/events/events.js'),
      util: utilShimPath,
      'node:util': utilShimPath,
    },
  },
  build: {
    outDir: process.env.BMS_SHARE_BUILD_OUT_DIR?.trim()
      ? path.resolve(__dirname, process.env.BMS_SHARE_BUILD_OUT_DIR)
      : path.resolve(__dirname, 'dist-share'),
    emptyOutDir: true,
    // No source maps: the public server would otherwise publish the source tree.
    sourcemap: false,
    // The share server's CSP allows only same-origin scripts; never inline them.
    assetsInlineLimit: 0,
    rollupOptions: {
      input: path.resolve(__dirname, 'share.html'),
    },
  },
})
