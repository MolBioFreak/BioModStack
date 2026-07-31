import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'jsdom',
        setupFiles: ['./tests/vitest/setup.ts'],
        include: [
            './tests/vitest/mdResultsMolstarMounted.test.tsx',
            './tests/vitest/mdTrajectoryFrameControls.test.tsx',
            './tests/vitest/mdQueuePanel.test.tsx',
            './tests/vitest/boltzApiNativeControls.test.tsx',
            './tests/vitest/stateLandscapeWorkspacePanel.test.tsx',
            './tests/vitest/bioxpCockpitMounted.test.tsx',
            './tests/vitest/bioxpCameraMounted.test.tsx',
            './tests/vitest/bioxpOperatorGenerationPayload.test.ts',
            './tests/vitest/bioxpOperatorCriticalControlsMounted.test.tsx',
            './tests/vitest/ontInstrumentPanel.test.tsx',
            './tests/vitest/conformationalMappingViewerBehavior.test.tsx',
        ],
    },
});
