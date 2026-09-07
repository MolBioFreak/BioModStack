import { defineConfig, mergeConfig } from 'vitest/config';
import base from './vitest.md.config';

// These suites deliberately require freshly generated ASGI wires; never hide
// them from collection when the caller forgot the fixture environment.
export default mergeConfig(base, defineConfig({test: {include: [
    './tests/vitest/closeoutAnalyticsMounted.test.tsx',
    './tests/vitest/scientificStructureBytesMounted.test.tsx',
]}}));
