import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import crypto from 'node:crypto'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'path'

const require = createRequire(import.meta.url)
const utilShimPath = path.resolve(__dirname, 'src/shims/util.ts')
const stablePdbeMolstarPath = path.dirname(require.resolve('pdbe-molstar-stable/package.json'))

function resolveViteCacheDir(): string {
  const explicitCacheDir = process.env.BMS_VITE_CACHE_DIR?.trim()
  if (explicitCacheDir) return path.resolve(explicitCacheDir)

  const uid = typeof process.getuid === 'function' ? process.getuid().toString() : 'unknown'
  const repoKey = crypto.createHash('sha1').update(__dirname).digest('hex').slice(0, 12)

  if (uid === '0') {
    return path.join(os.tmpdir(), 'biomodstack-vite-cache', `uid-${uid}`, repoKey)
  }

  const xdgCacheHome = process.env.XDG_CACHE_HOME?.trim()
  const homeCacheDir = process.env.HOME?.trim() ? path.join(process.env.HOME, '.cache') : undefined
  const cacheBase = xdgCacheHome || homeCacheDir || path.resolve(__dirname, '.cache')
  return path.join(cacheBase, 'biomodstack', 'frontend-vite', `uid-${uid}`, repoKey)
}

const isExpectedPdbeMolstarEvalWarning = (warning: { code?: string; id?: string; message?: string }) =>
  warning.code === 'EVAL' &&
  (warning.id?.includes('pdbe-molstar') ||
    warning.id?.includes('pdbe-molstar-stable') ||
    warning.message?.includes('pdbe-molstar-component.js'))

const normalizeChunkId = (id: string) => id.split(path.sep).join('/')

function manualChunks(id: string): string | undefined {
  const normalized = normalizeChunkId(id)

  // Keep Rollup/CommonJS helper modules with React. If these helpers fall into
  // the generic vendor chunk, vendor-react can import vendor while vendor also
  // imports vendor-react/Plotly, producing a production-only circular init crash.
  if (id.includes('\0commonjsHelpers.js') || normalized.includes('commonjsHelpers.js')) return 'vendor-react'

  // Keep large generated MolBio demo data out of the route/app chunk so the
  // sequence editor can request it only when that workspace is opened.
  if (normalized.includes('demoConstructs.generated')) return 'molbio-demo-data'

  if (!normalized.includes('/node_modules/')) return undefined

  if (
    normalized.includes('/node_modules/react/') ||
    normalized.includes('/node_modules/react-dom/') ||
    normalized.includes('/node_modules/react-router-dom/') ||
    normalized.includes('/node_modules/@tanstack/react-query/')
  ) {
    return 'vendor-react'
  }

  if (
    normalized.includes('/node_modules/@blueprintjs/') ||
    normalized.includes('/node_modules/@popperjs/')
  ) {
    return 'vendor-blueprint'
  }

  if (
    normalized.includes('/node_modules/plotly.js-dist-min/') ||
    normalized.includes('/node_modules/react-plotly.js/') ||
    normalized.includes('/node_modules/@plotly/') ||
    normalized.includes('/node_modules/plotly.js/') ||
    normalized.includes('/node_modules/d3-') ||
    normalized.includes('/node_modules/d3/')
  ) {
    return 'vendor-plotly'
  }

  if (normalized.includes('/node_modules/seqviz/')) return 'vendor-seqviz'

  if (normalized.includes('/node_modules/igv/')) return 'vendor-igv'

  if (
    normalized.includes('/node_modules/pdbe-molstar') ||
    normalized.includes('/node_modules/molstar')
  ) {
    return 'vendor-molstar'
  }

  if (
    normalized.includes('/node_modules/buffer/') ||
    normalized.includes('/node_modules/safe-buffer/') ||
    normalized.includes('/node_modules/string_decoder/') ||
    normalized.includes('/node_modules/events/')
  ) {
    return 'vendor-node-shims'
  }

  // Do not force every remaining dependency into one generic vendor chunk. That
  // can create a hard circular dependency between React and Plotly/common helper
  // chunks in production. Let Rollup place residual shared dependencies safely.
  return undefined
}

const devApiTarget = process.env.BMS_DEV_API_PROXY_TARGET || 'http://127.0.0.1:8002'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  // Use /bms/ for production (Tailscale Serve proxy), but / for dev mode
  base: mode === 'production' ? '/bms/' : '/',
  cacheDir: resolveViteCacheDir(),
  plugins: [react(), tailwindcss()],
  optimizeDeps: {
    include: [
      '@blueprintjs/icons',
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
      'safe-buffer': path.resolve(__dirname, 'node_modules/safe-buffer/index.js'),
      string_decoder: path.resolve(__dirname, 'node_modules/string_decoder/lib/string_decoder.js'),
      'string_decoder/': path.resolve(__dirname, 'node_modules/string_decoder'),
      events: path.resolve(__dirname, 'node_modules/events/events.js'),
      util: utilShimPath,
      'node:util': utilShimPath,
      // The newer 3.9.x PDBe bundle has been causing Chromium renderer crashes
      // on local structure views. Pin the frontend to the declared stable
      // 3.3.0 alias package until we can safely re-upgrade Molstar.
      'pdbe-molstar': stablePdbeMolstarPath,
    }
  },
  build: {
    // Keep verification artifacts outside deployment-owned dist trees.
    outDir: process.env.BMS_FRONTEND_BUILD_OUT_DIR?.trim()
      ? path.resolve(__dirname, process.env.BMS_FRONTEND_BUILD_OUT_DIR)
      : path.resolve(__dirname, 'dist'),
    emptyOutDir: true,
    // After route-level splitting, the remaining intentionally-large chunks are
    // scientific vendor bundles (Molstar/Plotly/IGV), not the initial app shell.
    // Keep the budget below the former monolithic app chunk so regressions are
    // still visible, but above the known stable Molstar vendor payload.
    chunkSizeWarningLimit: 6500,
    rollupOptions: {
      output: {
        manualChunks,
      },
      onwarn(warning, warn) {
        // The pinned PDBe Molstar 3.3.0 vendor bundle contains an eval shim.
        // Keep the warning scoped to this known bundle instead of hiding
        // Rollup warnings globally; the larger chunk-size warnings remain visible.
        if (isExpectedPdbeMolstarEvalWarning(warning)) return
        warn(warning)
      },
    },
  },
  server: {
    // Browser development owns Vite's documented default port. Keep it strict
    // so the stable hosted /bms/ surface cannot silently occupy the dev port.
    host: '127.0.0.1',
    port: 5173,
    origin: 'http://127.0.0.1:5173',
    strictPort: true,
    allowedHosts: ['compute-node.taileb3a90.ts.net'],
    // Prevent watching pipeline directories that can have millions of files
    watch: {
      ignored: [
        '**/.artifacts/**',
        '**/.test-dist/**',
        '**/dist/**',
        '**/tests/**',
        '**/work/**',
        '**/bms_results/**',
        '**/models/**',
        '**/apptainer/**',
        '**/binderscaffolds/**',
      ]
    },
    proxy: {
      // Proxy /api requests to backend server
      '/api': {
        target: devApiTarget,
        changeOrigin: true,
      }
    }
  }
}))
