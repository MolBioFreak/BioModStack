import React, { act } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, submitJob } from '../../src/lib/api';
import type { ExecutionPlanPreview } from '../../src/components/ExecutionPlanApproval';

const preview: ExecutionPlanPreview = {
    schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true,
    request: { model_id: 'boltz2', mode: 'complex', execution_target_id: 'vast:one' },
    plan: { requested_json: {}, effective_json: {},
        source_identity: { revision: 'b'.repeat(40), tree: 'c'.repeat(40) },
        metadata: { static_components: [], dynamic_templates: [], external_services: [] } },
    deferred_preparation: [], blockers: [],
    declared_expansions: [{ authority: 'native:mutation-seed', child_model: 'template_antibody_denovo',
        child_mode: 'antibody_refinement_pipeline', max_children: 1,
        selection_rule: 'all ingested designs from successful reviewed variants',
        root: { params: { boltz_sampling_steps: 111, seq_design_fampnn: true } },
        trigger: { param_overrides: { boltz_sampling_steps: 222 } } }],
};
const button = (text: string) => [...document.querySelectorAll('button')].find(row => row.textContent === text)!;
afterEach(() => { vi.restoreAllMocks(); document.body.innerHTML = ''; });

it('explicit parent review includes automatic child bounds and settings before any enqueue', async () => {
    const post = vi.spyOn(api, 'post').mockImplementation(async (url) => ({ data: url.endsWith('/preview') ? preview : { id: 'seed-batch' } }));
    const pending = submitJob({ name: 'seeds', ...preview.request, params: {} }, { launchContext: false });
    for (let i = 0; i < 150 && !button('Approve and submit'); i++) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
    }
    expect(post).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain('Automatic follow-on included in this approval');
    expect(document.body.textContent).toContain('No additional launch approval will be requested');
    expect(document.body.textContent).toContain('Up to 1');
    expect(document.body.textContent).toContain('antibody_refinement_pipeline');
    const details = document.querySelector('details')!;
    act(() => { details.open = true; });
    expect(details.textContent).toContain('parent.boltz_sampling_steps');
    expect(details.textContent).toContain('111');
    expect(details.textContent).toContain('overrides.boltz_sampling_steps');
    expect(details.textContent).toContain('222');
    await act(async () => { button('Approve and submit').click(); await pending; });
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][1]).toMatchObject({ execution_plan_approval: preview.approval_digest });
}, 15000);
