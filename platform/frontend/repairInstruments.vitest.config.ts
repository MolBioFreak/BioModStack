import { defineConfig } from 'vitest/config';
export default defineConfig({ cacheDir: './node_modules/.vite/repairInstruments', test: { environment: 'jsdom', setupFiles: ['./tests/vitest/setup.ts'], include: ['./tests/vitest/repairInstruments*.test.tsx'], maxWorkers: 1, testTimeout: 30000 } });
