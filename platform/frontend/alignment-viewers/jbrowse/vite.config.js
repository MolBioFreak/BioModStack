import { defineConfig } from 'vite'
export default defineConfig({ build: { outDir: 'dist', rollupOptions: { input: 'src.jsx', output: { inlineDynamicImports: true, entryFileNames: 'viewer.js', format: 'iife', name: 'BmsMsa' } } } })
