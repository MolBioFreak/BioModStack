import { defineConfig } from 'vitest/config';
export default defineConfig({ cacheDir: process.env.BMS_VITE_CACHE_DIR ?? '.artifacts/rfd4-vite', test: { environment: 'jsdom', setupFiles: ['./tests/vitest/setup.ts'], include: ['./tests/vitest/rfd4TaskWorkspaceMounted.test.tsx', './tests/vitest/complexaNativeSelection.test.ts'], passWithNoTests: false } });
