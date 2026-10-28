import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'jsdom', include: ['tests/vitest/alignmentEditorProject.test.tsx', 'tests/vitest/alignmentEditor.test.tsx', 'tests/vitest/alignmentCoordinates.test.ts', 'tests/vitest/alignmentWorkspace.test.tsx', 'tests/vitest/alignmentPage.test.tsx', 'tests/vitest/projectWorkflowSetup.test.tsx'] } })
