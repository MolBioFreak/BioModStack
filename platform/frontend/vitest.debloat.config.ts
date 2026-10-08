import base from './vitest.md.config';
export default { ...base, test: { ...base.test, include: [
    'tests/vitest/projectToolkitRoutesDebloat.test.tsx',
    'tests/vitest/projectMockWorkWire.test.ts',
    'tests/vitest/ngsProjectPanelMounted.test.tsx',
    'tests/vitest/molBioProjectHubMounted.test.tsx',
    'tests/vitest/projectReturnBanner.test.tsx',
    'tests/vitest/domainWorkflowOperatorClosure.test.ts',
] } };
