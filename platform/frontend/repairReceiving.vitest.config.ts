import { defineConfig } from 'vitest/config';
export default defineConfig({cacheDir:'./node_modules/.vite/repairReceiving',test:{environment:'jsdom',setupFiles:['./tests/vitest/setup.ts'],include:['tests/vitest/repairReceiving*.test.tsx'],maxWorkers:1,testTimeout:900000}});
