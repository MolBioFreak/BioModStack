import { defineConfig } from 'vitest/config';
export default defineConfig({
    cacheDir: process.env.BMS_VITE_CACHE_DIR ?? '.artifacts/other-denovo-vite',
    test: { environment: 'jsdom', setupFiles: ['./tests/vitest/setup.ts'], include: ['./tests/vitest/otherDeNovo*Mounted.test.tsx'] },
});
