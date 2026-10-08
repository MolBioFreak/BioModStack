import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'jsdom',
        setupFiles: ['./tests/vitest/setup.ts'],
        include: ['./tests/vitest/mdResultsMolstarMounted.test.tsx'],
    },
});
