import { defineConfig } from 'vitest/config';
export default defineConfig({ test: { environment: 'jsdom', setupFiles: ['./tests/vitest/setup.ts'], include: ['tests/vitest/repairWorkspace*.test.tsx', 'tests/vitest/bioxpMethodsMounted.test.tsx'], maxWorkers: 1, testTimeout: 30000 } });
