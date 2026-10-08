import { defineConfig } from 'vitest/config';

export default defineConfig({
    cacheDir: process.env.BMS_SCIENTIFIC_VITE_CACHE,
    test: {
        environment: 'jsdom',
        setupFiles: ['./tests/vitest/setup.ts'],
        include: ['./tests/vitest/frustraMpnnPlotlyAnalytics.test.tsx'],
    },
});
