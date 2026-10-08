import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const panel = readFileSync(
    new URL('../src/components/MolBioToolkit/panels/AssemblyPanel.tsx', import.meta.url),
    'utf8',
);

describe('Golden Gate restriction authority', () => {
    it('submits only stable catalog enzyme identity and displays backend option metadata', () => {
        expect(api).toContain('enzyme_id?: string');
        expect(api).not.toContain('enzyme_name?: string');
        expect(api).not.toMatch(/GoldenGateAssemblyOptionsResponse[\s\S]{0,300}site: string/);
        expect(panel).toContain('enzyme_id: goldenGateEnzyme');
        expect(panel).not.toContain('enzyme_name: goldenGateEnzyme');
        expect(panel).toContain('enzyme.canonical_name');
        expect(panel).not.toContain('enzyme.site');
    });
});
