import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import crypto from 'node:crypto'
import os from 'node:os'
import path from 'path'

const utilShimPath = path.resolve(__dirname, 'src/shims/util.ts')

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

  if (normalized.includes('/node_modules/molstar')) return 'vendor-molstar'

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

function molstarCommonJsBuildResolver(): Plugin {
  return {
    name: 'bms-molstar-commonjs-build-resolver',
    enforce: 'pre' as const,
    apply: 'build' as const,
    async resolveId(source, importer, options) {
      if (!source.startsWith('molstar/lib/') || source.startsWith('molstar/lib/commonjs/')) {
        return null
      }

      return this.resolve(
        source.replace(/^molstar\/lib\//, 'molstar/lib/commonjs/'),
        importer,
        { ...options, skipSelf: true },
      )
    },
  }
}

const devApiTarget = process.env.BMS_DEV_API_PROXY_TARGET || 'http://127.0.0.1:8002'
const devApiProxySecret = process.env.BMS_DEV_API_PROXY_SECRET?.trim() || ''
const devMk1dReconnectProxySecret = process.env.BMS_DEV_MK1D_RECONNECT_PROXY_SECRET?.trim() || ''
const buildRevision = /^[0-9a-f]{40}$/.test(process.env.VITE_BMS_BUILD_SHA?.trim() || '')
  ? process.env.VITE_BMS_BUILD_SHA!.trim()
  : 'unknown'
const buildMetadata = {
  layer: 'frontend',
  revision: buildRevision,
  buildId: process.env.VITE_BMS_BUILD_ID?.trim() || 'development',
  buildTime: process.env.VITE_BMS_BUILD_TIME?.trim() || 'unknown',
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  // Use /bms/ for production (Tailscale Serve proxy), but / for dev mode
  base: mode === 'production' ? '/bms/' : '/',
  cacheDir: resolveViteCacheDir(),
  plugins: [molstarCommonJsBuildResolver(), react(), tailwindcss()],
  define: {
    __BMS_BUILD_METADATA__: JSON.stringify(buildMetadata),
  },
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
    }
  },
  build: {
    // Never require write access to the deployed `dist/` tree just to verify a
    // candidate build. Release tooling can select a private staging directory;
    // promotion into a deployment-owned directory remains an explicit action.
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
      preserveEntrySignatures: false,
      output: {
        manualChunks,
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
      // Reconnect is available only on this local development proxy. The
      // Tailnet ingress has an explicit deny route and never receives this key.
      '/api/ont/devices/reconnect': {
        target: devApiTarget,
        changeOrigin: true,
        // The fixed helper transaction may wait up to 120 seconds for MinKNOW.
        // Keep the local proxy alive for that bounded response.
        timeout: 130_000,
        proxyTimeout: 130_000,
        ...(devMk1dReconnectProxySecret ? { headers: { 'X-BMS-MK1D-Reconnect-Proxy-Secret': devMk1dReconnectProxySecret } } : {}),
      },
      // Proxy other API requests to backend server
      '/api': {
        target: devApiTarget,
        changeOrigin: true,
        ...(devApiProxySecret ? { headers: { 'X-BMS-CM-Proxy-Secret': devApiProxySecret } } : {}),
      }
    }
  }
}))
