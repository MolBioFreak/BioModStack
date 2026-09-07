import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

it('renders the native-data to scorer to SQLite API response without recomputation', async () => {
    const path = process.env.BMS_G09_FRONTEND_FIXTURE;
    expect(path, 'run the G09 native API fixture first').toBeTruthy();
    const fixture = JSON.parse(readFileSync(path!, 'utf8'));
    expect(fixture.synthetic).toBe(true);
    const { MaturationEvidence } = await import('../../src/components/MaturationEvidence');
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    try {
        await act(async () => root.render(<MaturationEvidence comparisons={fixture.response.confidence_metrics.maturation_comparisons} completeness={fixture.response.ppiflow_completeness} />));
        expect(host.textContent).toContain('Reference: 2 / 2');
        expect(host.textContent).toContain('Candidate: 2 / 2');
        expect(host.textContent).toContain('Reference: 1 / 1');
        expect(host.textContent).toContain('Full-domain RMSD: 0');
    } finally {
        await act(async () => root.unmount());
        host.remove();
    }
});

const modules = import.meta.glob('../../src/components/MaturationEvidence.tsx');

it('shows both coverage denominators, unmatched identities, distinct subset, and exact rank reason', async () => {
    const load = modules['../../src/components/MaturationEvidence.tsx'];
    expect(load, 'existing result coverage presentation is missing').toBeTypeOf('function');
    const { MaturationEvidence } = await load() as { MaturationEvidence: React.ComponentType<{ comparisons: unknown; completeness: unknown }> };
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    try {
        await act(async () => root.render(<MaturationEvidence comparisons={{ whole_binder: {
            value: null, reason: 'incomplete_correspondence', matched_count: 1,
            expected_reference_count: 2, expected_candidate_count: 3,
            reference_coverage: 0.5, candidate_coverage: 0.3333333333333333,
            unmatched_reference: [{ identity: ['H', 2, 'A'], reason: 'unmapped_identity' }],
            unmatched_candidate: [{ identity: ['B', 9, ''], reason: 'missing_coordinates' }],
            subset: { name: 'explicit_matched_core', value: 1.25, unit: 'angstrom' },
        }}} completeness={{ paper_rank_available: false, paper_rank_reason_code: 'missing_iptm_evidence' }} />));
        expect(host.textContent).toContain('incomplete_correspondence');
        expect(host.textContent).toContain('Reference: 1 / 2');
        expect(host.textContent).toContain('Candidate: 1 / 3');
        expect(host.textContent).toContain('["H",2,"A"]');
        expect(host.textContent).toContain('missing_coordinates');
        expect(host.textContent).toContain('Subset explicit_matched_core: 1.25');
        expect(host.textContent).toContain('not full-domain RMSD');
        expect(host.textContent).toContain('missing_iptm_evidence');
        expect(host.textContent).not.toContain('Full-domain RMSD: 1.25');
        await act(async () => root.render(<MaturationEvidence comparisons={{}} completeness={{ paper_rank_available: false, paper_rank_reason_code: 'interface_scope_mismatch' }} />));
        expect(host.textContent).not.toContain('explicit_matched_core');
        expect(host.textContent).toContain('interface_scope_mismatch');
    } finally {
        await act(async () => root.unmount());
        host.remove();
    }
});
