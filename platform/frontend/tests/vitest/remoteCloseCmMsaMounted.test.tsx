import assert from 'node:assert/strict';
import { afterEach, test, vi } from 'vitest';
import React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ConformationalMappingLauncher } from '../../src/components/conformationalMapping/ConformationalMappingLauncher';
import { MSA_POLICY, hydrateHostedMsaSettings } from '../../src/lib/msaPolicy';
import { api } from '../../src/lib/api';
import type { CmSubmitRequest } from '../../src/components/conformationalMapping/conformationalMappingApi';
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
const text = (n: ReactTestInstance): string => n.children.map(c => typeof c === 'string' ? c : text(c)).join('');
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)); }); };
const key = 'bms.conformational-mapping.launcher.v1';
const defaults = hydrateHostedMsaSettings();
const changed = { ...defaults, msa_provider: 'neurosnap_api' as const, colabfold_use_env: false, colabfold_use_filter: false,
    colabfold_use_templates: true, colabfold_pairing_mode: 'unpaired_paired' as const, colabfold_pairing_strategy: 'complete' as const,
    msa_neurosnap_coverage_percent: 45.5, msa_neurosnap_identity_percent: 65.5, msa_neurosnap_max_sequences: 321,
    msa_neurosnap_force_uppercase: false, msa_neurosnap_pad_sequences: false };
async function mount(initialValues: Record<string, unknown> = {}) {
    const submissions: CmSubmitRequest[] = [], drafts: Record<string, unknown>[] = [];
    const post = vi.spyOn(api, 'post').mockImplementation(async (url, value) => {
        assert.equal(url, '/api/conformational-mapping/requests');
        submissions.push(value as CmSubmitRequest);
        throw new Error('offline HTTP boundary: retry same request');
    });
    let resolveConfig!: (v: unknown) => void;
    const config = new Promise(r => { resolveConfig = r; });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    let renderer!: ReactTestRenderer;
    await act(async () => { renderer = create(<MemoryRouter><QueryClientProvider client={client}>
        <ConformationalMappingLauncher initialValues={initialValues} onDraftChange={v => drafts.push(v)} services={{
            listSources: async () => [{ source_id: 'snapshot', source_kind: 'complex_snapshot', format: 'json', sha256: 'a'.repeat(64), bytes: 10,
                metadata: { target_ids: ['target-a'] }, authority_receipt: { schema_name: 'cm_source_authority_receipt', schema_version: 1,
                    source_id: 'snapshot', source_kind: 'complex_snapshot', content_sha256: 'a'.repeat(64), authority_kind: 'complex_snapshot_normalization', receipt_sha256: 'b'.repeat(64), payload: { target_ids: ['target-a'] } } }],
            loadFrustrampnnIntegration: () => config,
        } as never} />
    </QueryClientProvider></MemoryRouter>); });
    await flush();
    return { renderer, submissions, drafts, resolveConfig, close: async () => { await act(async () => renderer.unmount()); client.clear(); post.mockRestore(); } };
}
function labelText(node: ReactTestInstance): string {
    return node.children.map(child => typeof child === 'string' ? child : labelText(child as ReactTestInstance)).join('');
}
function control(renderer: ReactTestRenderer, label: string) {
    const row = renderer.root.findAllByType('label').find(n => labelText(n).startsWith(label));
    assert.ok(row, label);
    return row.find(n => n.type === 'input' || n.type === 'select');
}
async function edit(renderer: ReactTestRenderer, label: string, value: unknown) {
    await act(async () => control(renderer, label).props.onChange({ target: { value, checked: value, valueAsNumber: value } }));
    await flush();
}
afterEach(() => sessionStorage.clear());

test('mounted CM edits every global setting, preserves inactive values, session remount, clone, async config and retry identity', async () => {
    sessionStorage.clear();
    let m = await mount({ registered_snapshot_id: 'snapshot', ordered_seeds: [7, 9], samples_per_seed: 2 });
    assert.deepEqual(JSON.parse(sessionStorage.getItem(key)!).msaSettings, defaults);
    for (const [name, field] of Object.entries({ ...MSA_POLICY.colabfold_settings, ...MSA_POLICY.neurosnap_settings })) {
        // Exercise every actual shared input, including unsupported boolean intent;
        // restore consumer-compatible values without silently changing them.
        const value = changed[name as keyof typeof changed];
        if (field.type === 'boolean') await edit(m.renderer, field.native_name, !value);
        await edit(m.renderer, field.native_name, value);
    }
    await edit(m.renderer, 'MSA provider', 'neurosnap_api');
    await edit(m.renderer, 'Templates', true);
    await edit(m.renderer, 'RNA MSA', true);
    assert.match(text(m.renderer.root), /RNA MSA is selected and preserved/);
    await edit(m.renderer, 'RNA MSA', false);
    m.resolveConfig({ workflows: { conformational_mapping: { default_enabled: true, enabled_summary: 'Required.' } } });
    await flush();
    assert.deepEqual(JSON.parse(sessionStorage.getItem(key)!).msaSettings, changed);
    const draft = m.drafts.at(-1)!;
    const request = draft.request as CmSubmitRequest;
    assert.deepEqual(request.feature_policy.msa_settings, changed);
    assert.deepEqual(request.ordered_seeds, [7, 9]);
    assert.equal(request.feature_policy.templates_enabled, true);
    await m.close();
    m = await mount(); // actual session readback
    assert.deepEqual(JSON.parse(sessionStorage.getItem(key)!).msaSettings, changed);
    await m.close();
    sessionStorage.clear();
    m = await mount(draft); // Project clone/template readback
    m.resolveConfig({ workflows: { conformational_mapping: { default_enabled: true, enabled_summary: 'Required.' } } });
    await flush();
    assert.deepEqual(JSON.parse(sessionStorage.getItem(key)!).msaSettings, changed);
    for (let i = 0; i < 2; i++) {
        const button = m.renderer.root.findAllByType('button').find(n => /Launch conformational mapping/i.test(text(n)))!;
        await act(async () => button.props.onClick()); await flush();
    }
    assert.equal(m.submissions.length, 2);
    assert.deepEqual(m.submissions[0], m.submissions[1]);
    assert.deepEqual(m.submissions[0].feature_policy.msa_settings, changed);
    await m.close();
});

test('inactive protein MSA retains saved Neurosnap consumer-incompatible choices', async () => {
    sessionStorage.clear();
    const settings = { ...changed, msa_neurosnap_force_uppercase: true, msa_neurosnap_pad_sequences: true };
    const m = await mount({ feature_policy: { mode: 'features_disabled_control_v1', protein_msa_enabled: false,
        templates_enabled: false, rna_msa_enabled: false, msa_settings: settings } });
    assert.equal(control(m.renderer, 'Protein MSA').props.checked, false);
    assert.equal(control(m.renderer, 'Force Uppercase').props.checked, true);
    assert.equal(control(m.renderer, 'Pad Sequences').props.checked, true);
    const request = m.drafts.at(-1)!.request as CmSubmitRequest;
    assert.deepEqual(request.feature_policy.msa_settings, settings);
    assert.equal(request.feature_policy.protein_msa_enabled, false);
    await m.close();
});

test('saved incompatible feature flags remain visible and require correction rather than silent disabling', async () => {
    sessionStorage.clear();
    const m = await mount({ feature_policy: { mode: 'features_disabled_control_v1', protein_msa_enabled: true,
        templates_enabled: true, rna_msa_enabled: true, msa_settings: changed } });
    assert.equal(control(m.renderer, 'Protein MSA').props.checked, true);
    assert.equal(control(m.renderer, 'RNA MSA').props.checked, true);
    assert.equal(control(m.renderer, 'Templates').props.checked, true);
    assert.match(text(m.renderer.root), /feature-disabled control cannot enable MSA or templates/);
    assert.deepEqual(JSON.parse(sessionStorage.getItem(key)!).msaSettings, changed);
    await m.close();
});

test('canonical ConforNets shares controls and nested provider persistence without changing native skip intent', async () => {
    sessionStorage.clear();
    const m = await mount({ backend: 'confornets', request: { backend: 'confornets', feature_policy: { mode: 'regenerate_mutated_protein_v1', msa_settings: changed }, confornets: { skip_msa: false } } });
    assert.equal(control(m.renderer, 'MSA provider').props.value, 'neurosnap_api');
    const request = m.drafts.at(-1)!.request as CmSubmitRequest;
    assert.deepEqual(request.feature_policy.msa_settings, changed);
    assert.equal(request.confornets!.skip_msa, false);
    assert.equal(request.feature_policy.protein_msa_enabled, undefined);
    await m.close();
});
