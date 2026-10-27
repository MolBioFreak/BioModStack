import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'jsdom', include: ['tests/vitest/alignmentCoordinates.test.ts', 'tests/vitest/alignmentWorkspace.test.tsx'] } })
