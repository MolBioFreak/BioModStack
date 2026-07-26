import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { act, create, type ReactTestInstance } from 'react-test-renderer';
import type { AxiosResponse } from 'axios';
import { api } from '../src/lib/api';
import { AssemblyPanel } from '../src/components/MolBioToolkit/panels/AssemblyPanel';
import { buildDnaWeaverOrderContent } from '../src/components/MolBioToolkit/panels/dnaWeaverOrderExport';
import { LatestRequestGeneration } from '../src/components/MolBioToolkit/panels/latestRequestGeneration';

const panel = readFileSync(new URL('../src/components/MolBioToolkit/panels/AssemblyPanel.tsx', import.meta.url), 'utf8');
const workspacePath = new URL('../src/components/MolBioToolkit/panels/GibsonDesignWorkspace.tsx', import.meta.url);
const apiSource = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const orderExport = readFileSync(new URL('../src/components/MolBioToolkit/panels/dnaWeaverOrderExport.ts', import.meta.url), 'utf8');

test('Gibson defaults to target-first DNA Weaver planning while retaining validation and optional PCR', () => {
    assert.match(panel, /Plan vendor fragments \(DNA Weaver\)/);
    assert.match(panel, /Validate purchased fragments/);
    assert.match(panel, /PCR template route \(optional\)/);
    assert.match(panel, /Exact purchase sequence and source-core interval/);
});

test('DNA Weaver plan exposes constraints, QC, exports, and server-authoritative save', () => {
    assert.match(panel, /Minimum core bp/);
    assert.match(panel, /quality_checks/);
    assert.match(panel, /Export order FASTA/);
    assert.match(panel, /Export order CSV/);
    assert.match(orderExport, /sequence_sha256=/);
    assert.match(panel, /setDnaWeaverPlanState\(null\);/);
    assert.match(panel, /saveDnaWeaverGibsonAssembly/);
    assert.match(panel, /selected_plan_checksum/);
    assert.match(panel, /Regenerate \+ Verify \+ Save/);
    assert.match(apiSource, /\/api\/molbio\/assembly\/gibson\/dnaweaver\/save/);
    assert.match(apiSource, /plan_checksum/);
});

test('assembly payload topology follows the current construct instead of forcing circular', () => {
    const occurrences = panel.match(/circular: sequenceData\.circular/g) || [];
    assert.ok(occurrences.length >= 4, `expected topology propagation in planner and three assembly modes, found ${occurrences.length}`);
    assert.doesNotMatch(panel, /circular: true/);
});

test('Gibson design workspace exposes design, primer review, preview, and explicit save', () => {
    const workspace = readFileSync(workspacePath, 'utf8');
    assert.match(workspace, /Design & Simulate/);
    assert.match(workspace, /Generated primers/);
    assert.match(workspace, /Load preview/);
    assert.match(workspace, /Save as new construct/);
    assert.match(workspace, /selected_candidate_checksum/);
    assert.match(workspace, /initialCircular/);
    assert.match(workspace, /requestScopeRef\.current === scope/);
    assert.match(workspace, /primers: result\.primers\.map/);
    assert.match(workspace, /Array\.isArray\(detail\)/);
});

test('late DNA Weaver responses cannot republish through an A to B to A cycle', () => {
    const gate = new LatestRequestGeneration();
    gate.reconcileScope('A');
    const firstA = gate.begin();
    gate.reconcileScope('B');
    gate.reconcileScope('A');
    const secondA = gate.begin();
    assert.equal(gate.isCurrent(firstA), false);
    assert.equal(gate.isCurrent(secondA), true);
    assert.match(panel, /sequenceData\.version/);
});

function renderedText(node: ReactTestInstance): string {
    return node.children.map((child) => (
        typeof child === 'string' ? child : renderedText(child)
    )).join('');
}

test('AssemblyPanel suppresses a deferred plan after real Gibson plan to validate to plan transition', async () => {
    Object.assign(globalThis, { React, IS_REACT_ACT_ENVIRONMENT: true });
    const originalAdapter = api.defaults.adapter;
    let resolvePlan: ((value: AxiosResponse) => void) | null = null;
    api.defaults.adapter = (async (config) => {
        if (config.url?.endsWith('/dnaweaver/plan')) {
            return await new Promise((resolve) => { resolvePlan = resolve; });
        }
        if (config.url?.endsWith('/assembly-workups')) {
            return { data: [], status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.url?.endsWith('/golden-gate/options')) {
            return { data: { enzymes: [] }, status: 200, statusText: 'OK', headers: {}, config };
        }
        throw new Error(`Unexpected AssemblyPanel request: ${config.url}`);
    }) as typeof api.defaults.adapter;

    const sequenceData = {
        name: 'Race target', description: '', sequence: 'ACGT'.repeat(250), circular: false,
        sequenceType: 'dna' as const, features: [], primers: [], translations: [], analysisTracks: [],
        parentId: null, operation: null, operationParams: null, version: 7,
    };
    let renderer!: ReturnType<typeof create>;
    try {
        await act(async () => {
            renderer = create(React.createElement(AssemblyPanel, {
                sequenceData,
                selection: null,
                selectedSequenceId: 'target-1',
                onLoadProduct: () => undefined,
                onLoadSavedWorkup: () => undefined,
            }));
        });
        const button = (label: string) => renderer!.root.findAllByType('button').find(
            (candidate) => renderedText(candidate) === label,
        )!;
        await act(async () => { button('Gibson').props.onClick(); });
        await act(async () => { button('Plan vendor fragments').props.onClick(); });
        assert.ok(resolvePlan, 'planning request should be pending');
        await act(async () => { button('Validate purchased fragments').props.onClick(); });
        await act(async () => { button('Plan vendor fragments (DNA Weaver)').props.onClick(); });

        await act(async () => {
            resolvePlan!({
                data: {
                    planner_version: '0.3.10', validator_version: '5.5.16', estimated_price: 80,
                    estimated_lead_time_days: 10, ordered_fragments: [], pydna_exact_candidate_count: 1,
                    selected_product: { mode: 'gibson', sequence: sequenceData.sequence, length: sequenceData.sequence.length, circular: false, fragments: [], junctions: [], warnings: [], validation_notes: [] },
                    target_checksum: 'a'.repeat(64), plan_checksum: 'b'.repeat(64),
                    receipt_schema_version: 'dnaweaver-gibson-plan-v4', planner_implementation_revision: 'c'.repeat(40),
                    selected_product_checksum: 'd'.repeat(64),
                    target_attestation: { sequence_id: 'target-1', revision_id: 'revision-7', revision_number: 7, revision_sha256: 'a'.repeat(64) },
                    planning_parameters: {}, manufacturability_profile: 'generic_synthetic_dna_v1',
                    quality_checks: [], order_ready: true, warnings: [], validation_notes: [], message: 'late',
                },
                status: 200, statusText: 'OK', headers: {}, config: {},
            } as unknown as AxiosResponse);
        });

        const visible = renderer!.root.findAllByType('button').map(renderedText);
        assert.equal(visible.includes('Export order FASTA'), false);
        assert.equal(visible.includes('Export order CSV'), false);
        assert.equal(renderer!.root.findAll((node) => renderedText(node).includes('Plan SHA-256:')).length, 0);
    } finally {
        await act(async () => { renderer.unmount(); });
        api.defaults.adapter = originalAdapter;
    }
});

async function assertStaleSaveReleasesLoading(outcome: 'resolve' | 'reject' | 'overlap') {
    Object.assign(globalThis, { React, IS_REACT_ACT_ENVIRONMENT: true });
    const originalAdapter = api.defaults.adapter;
    const settleSaves: Array<(outcome: 'resolve' | 'reject') => void> = [];
    const sequenceData = {
        name: 'Save race target', description: '', sequence: 'ACGT'.repeat(250), circular: false,
        sequenceType: 'dna' as const, features: [], primers: [], translations: [], analysisTracks: [],
        parentId: null, operation: null, operationParams: null, version: 7,
    };
    const orderedFragments = [{
        id: 'fragment-1', name: 'fragment-1', sequence: 'ACGT', sequence_sha256: '1dff3e84fe7877e0673b69bbddcf40124e396e3f9943dd890c91b6a09adb9af0',
        orientation: 'forward', circular: false, role: 'source', metadata: { preparation: 'ready_linear', procurement: 'vendor_purchase' },
    }];
    const planData = {
        planner_version: '0.3.10', validator_version: '5.5.16', estimated_price: 80,
        estimated_lead_time_days: 10, ordered_fragments: orderedFragments, pydna_exact_candidate_count: 1,
        selected_product: { mode: 'gibson', sequence: sequenceData.sequence, length: sequenceData.sequence.length, circular: false, fragments: [], junctions: [], warnings: [], validation_notes: [] },
        target_checksum: 'a'.repeat(64), plan_checksum: 'b'.repeat(64),
        receipt_schema_version: 'dnaweaver-gibson-plan-v4', planner_implementation_revision: 'c'.repeat(40),
        selected_product_checksum: 'd'.repeat(64),
        target_attestation: { sequence_id: 'target-1', revision_id: 'revision-7', revision_number: 7, revision_sha256: 'a'.repeat(64) },
        planning_parameters: {}, manufacturability_profile: 'generic_synthetic_dna_v1',
        quality_checks: [], order_ready: true, warnings: [], validation_notes: [], message: 'planned',
    };
    api.defaults.adapter = (async (config) => {
        if (config.url?.endsWith('/dnaweaver/plan')) {
            return { data: planData, status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.url?.endsWith('/dnaweaver/save')) {
            return await new Promise((resolve, reject) => {
                settleSaves.push((resolution) => resolution === 'resolve'
                    ? resolve({ data: { ...planData, message: 'stale save' }, status: 200, statusText: 'OK', headers: {}, config })
                    : reject(new Error('stale save failed')));
            });
        }
        if (config.url?.endsWith('/assembly-workups')) {
            return { data: [], status: 200, statusText: 'OK', headers: {}, config };
        }
        if (config.url?.endsWith('/golden-gate/options')) {
            return { data: { enzymes: [] }, status: 200, statusText: 'OK', headers: {}, config };
        }
        throw new Error(`Unexpected AssemblyPanel request: ${config.url}`);
    }) as typeof api.defaults.adapter;

    let renderer!: ReturnType<typeof create>;
    try {
        await act(async () => {
            renderer = create(React.createElement(AssemblyPanel, {
                sequenceData, selection: null, selectedSequenceId: 'target-1',
                onLoadProduct: () => undefined, onLoadSavedWorkup: () => undefined,
            }));
        });
        const button = (label: string) => renderer.root.findAllByType('button').find(
            (candidate) => renderedText(candidate) === label,
        )!;
        await act(async () => { button('Gibson').props.onClick(); });
        await act(async () => { button('Plan vendor fragments').props.onClick(); });
        await act(async () => { button('Regenerate + Verify + Save').props.onClick(); });
        assert.equal(settleSaves.length, 1, 'authoritative save should be pending');
        assert.equal(renderedText(button('Saving…')), 'Saving…');

        const minimumCore = renderer.root.findAllByType('input').find((input) => input.props.value === 500)!;
        await act(async () => { minimumCore.props.onChange({ target: { value: '600' } }); });

        if (outcome === 'overlap') {
            assert.equal(button('Re-plan and validate').props.disabled, false);
            await act(async () => { button('Re-plan and validate').props.onClick(); });
            await act(async () => { button('Regenerate + Verify + Save').props.onClick(); });
            assert.equal(settleSaves.length, 2, 'new generation save should be pending');
            assert.equal(renderedText(button('Saving…')), 'Saving…');

            await act(async () => { settleSaves[0]('resolve'); });
            assert.equal(
                renderedText(button('Saving…')),
                'Saving…',
                'old save completion must not clear the newer save loading owner',
            );
            await act(async () => { settleSaves[1]('resolve'); });
        } else {
            await act(async () => { settleSaves[0](outcome); });
        }

        const replan = button('Re-plan and validate');
        assert.equal(replan.props.disabled, false, 'scope invalidation must release stale save loading ownership');
        assert.equal(renderer.root.findAllByType('button').some((candidate) => renderedText(candidate) === 'Saving…'), false);
        assert.equal(renderer.root.findAll((node) => renderedText(node).includes('stale save failed')).length, 0);
    } finally {
        await act(async () => { renderer.unmount(); });
        api.defaults.adapter = originalAdapter;
    }
}

test('AssemblyPanel releases loading after a stale authoritative save rejects', async () => {
    await assertStaleSaveReleasesLoading('reject');
});

test('AssemblyPanel releases loading after a stale authoritative save succeeds', async () => {
    await assertStaleSaveReleasesLoading('resolve');
});

test('AssemblyPanel keeps loading owned by a newer authoritative save', async () => {
    await assertStaleSaveReleasesLoading('overlap');
});

test('saved Gibson constructs expose persisted design and vendor-order evidence in Assembly', () => {
    assert.match(panel, /Saved Gibson workup/);
    assert.match(panel, /Server-selected candidate checksum/);
    assert.match(panel, /DNA Weaver plan checksum/);
    assert.match(panel, /operationParams\.ordered_fragments/);
    assert.match(panel, /Validated junctions/);
    assert.match(panel, /Generated primers/);
    assert.match(panel, /Export persisted order FASTA/);
    assert.match(panel, /Export persisted order CSV/);
});

test('persisted and freshly planned DNA Weaver evidence reproduce byte-identical orders', async () => {
    const planChecksum = 'b'.repeat(64);
    const sequenceSha256 = '1dff3e84fe7877e0673b69bbddcf40124e396e3f9943dd890c91b6a09adb9af0';
    const live = [{
        name: 'fragment-1', sequence: 'ACGT', sequence_sha256: sequenceSha256,
        metadata: { source_core_start: 0, source_core_end: 4, terminal_overlap_length: 30 },
    }];
    const persisted = [{
        name: 'fragment-1', sequence: 'ACGT', sequence_sha256: sequenceSha256,
        source_core_start: 0, source_core_end: 4, terminal_overlap_length: 30,
    }];
    for (const format of ['fasta', 'csv'] as const) {
        assert.equal(
            await buildDnaWeaverOrderContent(persisted, planChecksum, format),
            await buildDnaWeaverOrderContent(live, planChecksum, format),
        );
    }
});

test('persisted order export rejects sequence bytes that do not match the claimed hash', async () => {
    await assert.rejects(
        buildDnaWeaverOrderContent(
            [{ name: 'tampered', sequence: 'ACGT', sequence_sha256: 'a'.repeat(64) }],
            'b'.repeat(64),
            'fasta',
        ),
        /does not match its SHA-256 evidence/,
    );
});
