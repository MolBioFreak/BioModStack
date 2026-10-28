import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'jsdom', include: ['tests/vitest/alignment*.test.ts*', 'tests/vitest/biojsViewer.test.tsx', 'tests/vitest/projectWorkflowSetup.test.tsx'] } })
