import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { FrustraMpnnExperimentResults } from '../../src/components/frustrampnn/FrustraMpnnExperimentResults';
import type { FrustraMpnnExperimentScopeItem } from '../../src/lib/projectManager';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });
afterEach(() => document.body.replaceChildren());

const item = (state: FrustraMpnnExperimentScopeItem['state'], analysisState: FrustraMpnnExperimentScopeItem['statistics_analysis']['state'], index: number): FrustraMpnnExperimentScopeItem => ({
    result_receipt_id: `receipt-${index}`, parent_job_id: `job-${index}`, invocation_id: `invocation-${index}`,
    candidate_id: `candidate-${index}`, operator_label: `Structure ${index}`,
    source_identity: { design_id: `design-${index}`, artifact_id: `artifact-${index}`, artifact_sha256: 'a'.repeat(64), candidate_id: `candidate-${index}` },
    state, diagnostic: state === 'failed' ? 'inference failed' : null,
    statistics_analysis: { state: analysisState, diagnostic: analysisState === 'failed' ? 'analysis failed' : null },
    manifest_sha256: 'b'.repeat(64), content_digest: 'c'.repeat(64), reopen_uri: `/designs/job-${index}`,
});

describe('whole-experiment FrustraMPNN projection', () => {
    it('visibly renders every expected terminal state, statistics state, identity, diagnostic, and exact revisions', async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        const items = [
            item('completed', 'completed', 1), item('failed', 'not_started', 2), item('missing', 'not_started', 3),
            item('skipped', 'not_started', 4), item('completed', 'failed', 5),
        ];
        await act(async () => root.render(<FrustraMpnnExperimentResults
            items={items.map((entry) => ({ item: entry, href: `${entry.reopen_uri}?result_model=frustrampnn` }))}
            globalRevisionId="global-rev-4"
            domainRevisionId="domain-rev-7"
        />));
        expect(container.textContent).toContain('Global revision global-rev-4 · Domain revision domain-rev-7');
        for (const state of ['completed', 'failed', 'missing', 'skipped']) expect(container.textContent).toContain(state);
        expect(container.textContent).toContain('Design design-3 · Artifact artifact-3');
        expect(container.textContent).toContain('inference failed');
        expect(container.textContent).toContain('Statistics analysis: failed');
        expect(container.textContent).toContain('Analysis: analysis failed');
        expect(container.querySelectorAll('a')).toHaveLength(5);
        await act(async () => root.unmount());
    });
});
