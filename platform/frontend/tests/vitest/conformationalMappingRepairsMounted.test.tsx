import assert from 'node:assert/strict';
import { afterEach, test } from 'vitest';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import { ConformationalMappingLauncher } from '../../src/components/conformationalMapping/ConformationalMappingLauncher.js';
import { ConformationalMappingViewer } from '../../src/components/conformationalMapping/ConformationalMappingViewer.js';
import { JobDetailPage } from '../../src/components/JobDetailPage.js';
import { JobSubmission } from '../../src/components/JobSubmission.js';
import { ResultsViewer } from '../../src/components/ResultsViewer.js';
import {
    searchCmRcsb,
    type CmFailureReceipt,
    type CmRcsbSearchResponse,
    type CmReusableRun,
    type CmSource,
    type CmStatus,
    type CmSubmitRequest,
} from '../../src/components/conformationalMapping/conformationalMappingApi.js';
import type { FrustraMpnnSourceInspection } from '../../src/components/frustrampnn/frustraMpnnSettingsState.js';
import { api } from '../../src/lib/api.js';

const sha = (letter: string) => letter.repeat(64);
const text = (node: ReactTestInstance): string => node.children.map((child) => typeof child === 'string' ? child : text(child)).join('');
const flush = async (turns = 8) => {
    for (let index = 0; index < turns; index += 1) {
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    }
};
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
const LocationProbe = () => <span data-mounted-location={useLocation().pathname} />;

const snapshot: CmSource = {
    source_id: 'snapshot-source',
    source_kind: 'complex_snapshot',
    format: 'json',
    sha256: sha('a'),
    bytes: 2048,
    metadata: { name: 'Kinase complex', target_ids: ['target-a'] },
    authority_receipt: {
        schema_name: 'cm_source_authority_receipt', schema_version: 1,
        source_id: 'snapshot-source', source_kind: 'complex_snapshot', content_sha256: sha('a'),
        authority_kind: 'complex_snapshot_normalization', receipt_sha256: sha('b'),
        payload: {
            target_ids: ['target-a'], model_ids: ['model-1'], sample_ids: ['sample-1'],
            chain_ids: ['A', 'B'], entity_ids: ['entity-1', 'entity-2'],
        },
    },
};

const mountLauncher = async (services: Record<string, unknown>, initialValues: Record<string, unknown> = {}) => {
    sessionStorage.clear();
    const queryClient = client();
    const resolvedServices = {
        loadFrustrampnnIntegration: async () => ({
            workflows: {
                conformational_mapping: {
                    default_enabled: true,
                    enabled_summary: 'Required state-conditioned analysis.',
                },
            },
        }),
        ...services,
    };
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(
            <MemoryRouter><QueryClientProvider client={queryClient}>
                <ConformationalMappingLauncher services={resolvedServices as never} initialValues={initialValues} />
            </QueryClientProvider></MemoryRouter>,
        );
    });
    await flush();
    return { renderer: renderer!, client: queryClient };
};

const clickButton = async (renderer: ReactTestRenderer, label: RegExp) => {
    const button = renderer.root.findAllByType('button').find((item) => label.test(text(item)));
    assert.ok(button, `button ${String(label)} was not mounted`);
    await act(async () => button.props.onClick());
    await flush();
};

afterEach(() => sessionStorage.clear());

test('mounted launcher uses independent record/source and science/preview columns', async () => {
    const mounted = await mountLauncher({
        listSources: async () => [],
        loadFrustrampnnIntegration: async () => ({
            workflows: {
                conformational_mapping: {
                    default_enabled: true,
                    enabled_summary: 'Required state-conditioned analysis.',
                },
            },
        }),
    });

    const left = mounted.renderer.root.findByProps({ 'data-cm-launcher-column': 'record-source' });
    const right = mounted.renderer.root.findByProps({ 'data-cm-launcher-column': 'science-preview' });
    const leftText = text(left);
    const rightText = text(right);
    assert.ok(leftText.indexOf('Run record') < leftText.indexOf('Source browser'));
    assert.doesNotMatch(leftText, /Scientific controls|Input preview/);
    assert.ok(rightText.indexOf('Scientific controls') < rightText.indexOf('Input preview'));
    assert.doesNotMatch(rightText, /Run record|Source browser/);
    assert.match(
        rightText,
        /Region controls require one registered protein source\./,
    );

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});


test('mounted launcher hydrates and reports the exact Project-owned native draft', async () => {
    const drafts: Record<string, unknown>[] = [];
    sessionStorage.clear();
    const queryClient = client();
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(
            <MemoryRouter><QueryClientProvider client={queryClient}>
                <ConformationalMappingLauncher
                    services={{
                        listSources: async () => [],
                        loadFrustrampnnIntegration: async () => ({ workflows: { conformational_mapping: { default_enabled: true, enabled_summary: 'Required.' } } }),
                    } as never}
                    initialValues={{
                        backend: 'confornets',
                        request: {
                            backend: 'confornets', ordered_seeds: [73], samples_per_seed: 4,
                            confornets: { task: 'diversity', runs: 2, saved_steps: [100], confornet_count: 3, samples: 4, max_steps: 100 },
                        },
                    }}
                    onDraftChange={(draft) => drafts.push(draft)}
                />
            </QueryClientProvider></MemoryRouter>,
        );
    });
    await flush();
    assert.ok(drafts.length > 0);
    assert.equal(drafts.at(-1)?.backend, 'confornets');
    assert.deepEqual((drafts.at(-1)?.request as Record<string, unknown> | null)?.ordered_seeds, [73]);
    await act(async () => renderer!.unmount());
    queryClient.clear();
});


test('mounted launcher round-trips reference state-landscape authority into the typed request', async () => {
    const submissions: CmSubmitRequest[] = [];
    const mounted = await mountLauncher({
        listSources: async () => [snapshot],
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            return {
                request_id: 'request-reference', job_id: 'request-reference', status: 'queued',
                backend: payload.backend, request_sha256: sha('c'), coordinate_plan_sha256: sha('d'), expected_cardinality: 2,
            };
        },
    }, {
        name: 'Reference comparison', backend: 'protenix_v2_ensemble', registered_snapshot_id: snapshot.source_id,
        ordered_seeds: [101, 202], samples_per_seed: 1,
        state_landscape_comparison: {
            mode: 'reference', target_id: 'target-a', scope: 'all_other_within_target',
            reference_backend_coordinates: {
                backend: 'protenix_v2_ensemble', target_id: 'target-a', ordered_seed: 101, sample_index: 0,
            },
        },
    });

    assert.match(text(mounted.renderer.root), /Reference comparison/i);
    await clickButton(mounted.renderer, /Launch conformational mapping/i);
    assert.equal(submissions.length, 1);
    assert.deepEqual(submissions[0].state_landscape_comparison, {
        mode: 'reference', target_id: 'target-a', scope: 'all_other_within_target',
        reference_backend_coordinates: {
            backend: 'protenix_v2_ensemble', target_id: 'target-a', ordered_seed: 101, sample_index: 0,
        },
    });
    const persisted = JSON.parse(sessionStorage.getItem('bms.conformational-mapping.launcher.v1') || '{}');
    assert.equal(persisted.stateComparisonMode, 'reference');
    assert.equal(persisted.referenceOrderedSeed, 101);
    assert.equal(persisted.referenceSampleIndex, 0);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted launcher inspects external-import structures for FrustraMPNN scope', async () => {
    const externalSource: CmSource = {
        ...snapshot,
        source_id: 'external-structure',
        source_kind: 'structure_upload',
        format: 'mmcif',
        metadata: { name: 'Imported structure' },
        authority_receipt: {
            schema_name: 'cm_source_authority_receipt',
            schema_version: 1,
            source_id: 'external-structure',
            source_kind: 'structure_upload',
            content_sha256: sha('a'),
            authority_kind: 'run_artifact',
            receipt_sha256: sha('b'),
            payload: {},
        },
    };
    const inspectedSources: string[] = [];
    const mounted = await mountLauncher({
        listSources: async () => [externalSource],
        inspectFrustrampnnSource: async (sourceId: string) => {
            inspectedSources.push(sourceId);
            return {
                source_models: [1], selected_source_model: 1,
                observed_altlocs: [''], selected_altloc: '',
                protein_entities: [{
                    entity_instance_id: 'A', source_entity_id: '1',
                    label_asym_id: null, auth_asym_id: null, pdb_chain_id: null,
                }],
                protein_sequence_spans: [{
                    entity_instance_id: 'A', source_entity_id: '1',
                    label_asym_id: null, auth_asym_id: null,
                    sequence_start: 1, sequence_end: 25,
                }],
                mapped_residues: [],
            };
        },
    }, {
        name: 'External region analysis', backend: 'external_import',
        registered_artifact_ids: [externalSource.source_id],
        ordered_seeds: [0], samples_per_seed: 1,
    });

    assert.deepEqual(inspectedSources, [externalSource.source_id]);
    const mode = mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true });
    await act(async () => mode.props.onChange({ target: { value: 'selected_entities' } }));
    await flush();
    const sourceSelectorText = text(mounted.renderer.root);
    assert.match(sourceSelectorText, /Source instance A/i);
    assert.match(sourceSelectorText, /Scope:\s*Source instance A/i);
    assert.doesNotMatch(sourceSelectorText, /Chain null/i);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted launcher loads source-backed sequence spans and submits one exact FrustraMPNN region', async () => {
    const submissions: CmSubmitRequest[] = [];
    const inspection: FrustraMpnnSourceInspection = {
        source_models: [1],
        selected_source_model: 1,
        observed_altlocs: [''],
        selected_altloc: '',
        protein_entities: [{
            entity_instance_id: 'A', source_entity_id: '1', label_asym_id: null,
            auth_asym_id: null, pdb_chain_id: null,
        }],
        protein_sequence_spans: [{
            entity_instance_id: 'A', source_entity_id: '1', label_asym_id: null,
            auth_asym_id: null, sequence_start: 1, sequence_end: 50,
        }],
        mapped_residues: [],
    };
    const inspectedSources: string[] = [];
    const mounted = await mountLauncher({
        listSources: async () => [snapshot],
        loadFrustrampnnIntegration: async () => ({
            workflows: {
                conformational_mapping: {
                    default_enabled: true,
                    enabled_summary: 'Required state-conditioned analysis.',
                },
            },
        }),
        inspectFrustrampnnSource: async (sourceId: string) => {
            inspectedSources.push(sourceId);
            return inspection;
        },
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            return {
                request_id: 'request-region', job_id: 'request-region', status: 'queued',
                backend: payload.backend, request_sha256: sha('c'), coordinate_plan_sha256: sha('d'), expected_cardinality: 1,
            };
        },
    }, {
        name: 'Region analysis', backend: 'protenix_v2_ensemble',
        registered_snapshot_id: snapshot.source_id, ordered_seeds: [101], samples_per_seed: 1,
    });

    assert.deepEqual(inspectedSources, [snapshot.source_id]);
    const mode = mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true });
    await act(async () => mode.props.onChange({ target: { value: 'selected_regions' } }));
    await flush();
    const start = mounted.renderer.root.findByProps({ 'data-frustrampnn-region-start': true });
    await act(async () => start.props.onChange({ target: { value: '10' } }));
    await flush();
    await clickButton(mounted.renderer, /Launch conformational mapping/i);

    assert.deepEqual(submissions[0].frustrampnn_settings.protein_selection, {
        mode: 'selected_regions',
        entities: [],
        regions: [{
            entity_instance_id: 'A', source_entity_id: '1', label_asym_id: null,
            auth_asym_id: null, sequence_start: 10, sequence_end: 50,
        }],
        residues: [],
    });
    assert.deepEqual(submissions[0].frustrampnn_settings.source_structure, {
        selected_model_number: 1,
        preferred_altloc: '',
    });
    const persistedDraft = JSON.parse(
        sessionStorage.getItem('bms.conformational-mapping.launcher.v1') || '{}',
    ) as Record<string, unknown>;
    assert.equal(Object.hasOwn(persistedDraft, 'frustrampnnSelectionSourceId'), false);

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted launcher clears source-specific FrustraMPNN intent when the selected source changes', async () => {
    const secondSnapshot: CmSource = {
        ...snapshot,
        source_id: 'snapshot-source-2',
        sha256: sha('2'),
        metadata: { name: 'Second kinase complex', target_ids: ['target-b'] },
        authority_receipt: {
            ...snapshot.authority_receipt!,
            source_id: 'snapshot-source-2',
            content_sha256: sha('2'),
            receipt_sha256: sha('3'),
            payload: {
                target_ids: ['target-b'], model_ids: ['model-2'], sample_ids: ['sample-2'],
                chain_ids: ['A'], entity_ids: ['entity-2'],
            },
        },
    };
    const inspection = (sourceId: string): FrustraMpnnSourceInspection => ({
        source_models: [1], selected_source_model: 1,
        observed_altlocs: [''], selected_altloc: '',
        protein_entities: [{
            entity_instance_id: sourceId === snapshot.source_id ? 'A' : 'B',
            source_entity_id: sourceId === snapshot.source_id ? '1' : '2',
            label_asym_id: null, auth_asym_id: null, pdb_chain_id: null,
        }],
        protein_sequence_spans: [{
            entity_instance_id: sourceId === snapshot.source_id ? 'A' : 'B',
            source_entity_id: sourceId === snapshot.source_id ? '1' : '2',
            label_asym_id: null, auth_asym_id: null,
            sequence_start: 1, sequence_end: 50,
        }],
        mapped_residues: [],
    });
    const submissions: CmSubmitRequest[] = [];
    const mounted = await mountLauncher({
        listSources: async () => [snapshot, secondSnapshot],
        inspectFrustrampnnSource: async (sourceId: string) => inspection(sourceId),
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            return {
                request_id: 'request-source-switch', job_id: 'request-source-switch', status: 'queued',
                backend: payload.backend, request_sha256: sha('4'), coordinate_plan_sha256: sha('5'), expected_cardinality: 1,
            };
        },
    }, {
        name: 'Source switch', backend: 'protenix_v2_ensemble',
        registered_snapshot_id: snapshot.source_id, ordered_seeds: [101], samples_per_seed: 1,
    });

    const mode = mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true });
    await act(async () => mode.props.onChange({ target: { value: 'selected_regions' } }));
    await flush();
    assert.equal(mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true }).props.value, 'selected_regions');

    await clickButton(mounted.renderer, /^Cached$/i);
    await clickButton(mounted.renderer, /snapshot-source-2/i);
    assert.equal(
        mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true }).props.value,
        'all_protein_entities',
    );
    await clickButton(mounted.renderer, /Launch conformational mapping/i);
    assert.equal(submissions[0]?.registered_snapshot_id, secondSnapshot.source_id);
    assert.deepEqual(submissions[0]?.frustrampnn_settings.protein_selection, {
        mode: 'all_protein_entities', entities: [], regions: [], residues: [],
    });

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted launcher blocks reopened source-specific intent while source inspection is pending or failed', async () => {
    const submissions: CmSubmitRequest[] = [];
    let rejectInspection!: (reason?: unknown) => void;
    const pendingInspection = new Promise<FrustraMpnnSourceInspection>((_resolve, reject) => {
        rejectInspection = reject;
    });
    const mounted = await mountLauncher({
        listSources: async () => [snapshot],
        inspectFrustrampnnSource: async () => pendingInspection,
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            throw new Error('submission must remain unreachable');
        },
    }, {
        name: 'Reopened region', backend: 'protenix_v2_ensemble',
        registered_snapshot_id: snapshot.source_id, ordered_seeds: [101], samples_per_seed: 1,
        frustrampnn_settings: {
            schema_name: 'frustrampnn_settings', schema_version: 1,
            protein_selection: {
                mode: 'selected_regions', entities: [], residues: [],
                regions: [{
                    entity_instance_id: 'A', source_entity_id: '1', label_asym_id: null,
                    auth_asym_id: null, sequence_start: 10, sequence_end: 20,
                }],
            },
            source_structure: { selected_model_number: 1, preferred_altloc: '' },
            classification_policy: { mode: 'canonical', high_max: -1.0, minimal_min: 0.58 },
        },
    });

    const launch = mounted.renderer.root.findAllByType('button').find((item) => /Launch conformational mapping/i.test(text(item)));
    assert.ok(launch);
    assert.equal(launch.props.disabled, true);
    assert.match(text(mounted.renderer.root), /source-specific FrustraMPNN selection.*inspection/i);
    assert.match(text(mounted.renderer.root), /Resolving exact source sequence identities/i);
    assert.equal(submissions.length, 0);

    await act(async () => rejectInspection(new Error('inspection unavailable')));
    await flush();
    const failedLaunch = mounted.renderer.root.findAllByType('button').find((item) => /Launch conformational mapping/i.test(text(item)));
    assert.ok(failedLaunch);
    assert.equal(failedLaunch.props.disabled, true);
    assert.match(text(mounted.renderer.root), /source-specific FrustraMPNN selection.*inspection/i);
    assert.equal(submissions.length, 0);

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted launcher requires a selector edit before rebinding reopened source-specific intent', async () => {
    const inspection: FrustraMpnnSourceInspection = {
        source_models: [1], selected_source_model: 1,
        observed_altlocs: [''], selected_altloc: '',
        protein_entities: [{
            entity_instance_id: 'A', source_entity_id: '1',
            label_asym_id: null, auth_asym_id: null, pdb_chain_id: null,
        }],
        protein_sequence_spans: [{
            entity_instance_id: 'A', source_entity_id: '1',
            label_asym_id: null, auth_asym_id: null,
            sequence_start: 1, sequence_end: 50,
        }],
        mapped_residues: [],
    };
    const submissions: CmSubmitRequest[] = [];
    const mounted = await mountLauncher({
        listSources: async () => [snapshot],
        inspectFrustrampnnSource: async () => inspection,
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            return {
                request_id: 'request-rebound-region', job_id: 'request-rebound-region', status: 'queued',
                backend: payload.backend, request_sha256: sha('6'), coordinate_plan_sha256: sha('7'), expected_cardinality: 1,
            };
        },
    }, {
        name: 'Reopened region', backend: 'protenix_v2_ensemble',
        registered_snapshot_id: snapshot.source_id, ordered_seeds: [101], samples_per_seed: 1,
        frustrampnn_settings: {
            schema_name: 'frustrampnn_settings', schema_version: 1,
            protein_selection: {
                mode: 'selected_regions', entities: [], residues: [],
                regions: [{
                    entity_instance_id: 'A', source_entity_id: '1', label_asym_id: null,
                    auth_asym_id: null, sequence_start: 10, sequence_end: 20,
                }],
            },
            source_structure: { selected_model_number: 1, preferred_altloc: '' },
            classification_policy: { mode: 'canonical', high_max: -1.0, minimal_min: 0.58 },
        },
    });

    await flush();
    let launch = mounted.renderer.root.findAllByType('button').find((item) => /Launch conformational mapping/i.test(text(item)));
    assert.ok(launch);
    assert.equal(launch.props.disabled, true);
    assert.match(text(mounted.renderer.root), /source-specific FrustraMPNN selection is not bound/i);
    assert.equal(submissions.length, 0);

    const start = mounted.renderer.root.findByProps({ 'data-frustrampnn-region-start': true });
    await act(async () => start.props.onChange({ target: { value: '11' } }));
    await flush();
    launch = mounted.renderer.root.findAllByType('button').find((item) => /Launch conformational mapping/i.test(text(item)));
    assert.ok(launch);
    assert.equal(launch.props.disabled, false);
    await clickButton(mounted.renderer, /Launch conformational mapping/i);
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0]?.frustrampnn_settings.protein_selection.mode, 'selected_regions');
    if (submissions[0]?.frustrampnn_settings.protein_selection.mode === 'selected_regions') {
        assert.equal(submissions[0].frustrampnn_settings.protein_selection.regions[0]?.sequence_start, 11);
    }

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted launcher preserves an exact source binding across a same-source inspection refresh', async () => {
    const inspection: FrustraMpnnSourceInspection = {
        source_models: [1], selected_source_model: 1,
        observed_altlocs: [''], selected_altloc: '',
        protein_entities: [{
            entity_instance_id: 'A', source_entity_id: '1',
            label_asym_id: null, auth_asym_id: null, pdb_chain_id: null,
        }],
        protein_sequence_spans: [{
            entity_instance_id: 'A', source_entity_id: '1',
            label_asym_id: null, auth_asym_id: null,
            sequence_start: 1, sequence_end: 50,
        }],
        mapped_residues: [],
    };
    let inspectionCalls = 0;
    let resolveRefresh!: (value: FrustraMpnnSourceInspection) => void;
    const mounted = await mountLauncher({
        listSources: async () => [snapshot],
        inspectFrustrampnnSource: async () => {
            inspectionCalls += 1;
            if (inspectionCalls === 1) return inspection;
            return new Promise<FrustraMpnnSourceInspection>((resolve) => {
                resolveRefresh = resolve;
            });
        },
    }, {
        name: 'Same-source refresh', backend: 'protenix_v2_ensemble',
        registered_snapshot_id: snapshot.source_id, ordered_seeds: [101], samples_per_seed: 1,
    });

    const selectionMode = mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true });
    await act(async () => selectionMode.props.onChange({ target: { value: 'selected_regions' } }));
    await flush();
    await act(async () => {
        void mounted.client.invalidateQueries({
            queryKey: ['cm-frustrampnn-source-inspection', snapshot.source_id],
        });
    });
    await flush();
    assert.match(text(mounted.renderer.root), /Resolving exact source sequence identities/i);
    const classificationMode = mounted.renderer.root.findByProps({ 'data-frustrampnn-classification-mode': true });
    await act(async () => classificationMode.props.onChange({ target: { value: 'custom' } }));
    await act(async () => resolveRefresh(inspection));
    await flush();

    const launch = mounted.renderer.root.findAllByType('button').find((item) => /Launch conformational mapping/i.test(text(item)));
    assert.ok(launch);
    assert.equal(launch.props.disabled, false);
    assert.equal(
        mounted.renderer.root.findByProps({ 'data-frustrampnn-selection-mode': true }).props.value,
        'selected_regions',
    );

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});


test('mounted Your Runs discovers reusable artifacts and registers only an explicit authoritative choice', async () => {
    const runs: CmReusableRun[] = [{
        request_id: 'run-1', job_id: 'job-1', name: 'Completed kinase fold', workflow: 'conformational_mapping',
        status: 'completed', completed_at: '2026-08-08T12:00:00Z',
        artifacts: [{
            artifact_id: 'artifact-a', candidate_id: 'model-1', name: 'model-1', role: 'authoritative_cif', artifact_type: 'authoritative_cif', format: 'mmcif',
            media_type: 'chemical/x-mmcif', sha256: sha('e'), bytes: 4096, available: true,
            backend_coordinates: { backend: 'external_import', target_id: 'target-a', staged_index: 0, source_content_sha256: sha('e'), staged_receipt_sha256: sha('a') },
        }, {
            artifact_id: 'artifact-b', candidate_id: 'model-2', name: 'model-2', role: 'authoritative_cif', artifact_type: 'authoritative_cif', format: 'mmcif',
            media_type: 'chemical/x-mmcif', sha256: sha('f'), bytes: 8192, available: true,
            backend_coordinates: { backend: 'external_import', target_id: 'target-b', staged_index: 0, source_content_sha256: sha('f'), staged_receipt_sha256: sha('b') },
        }],
    }];
    const registrations: Array<[string, string]> = [];
    const submissions: CmSubmitRequest[] = [];
    const runSource: CmSource = {
        source_id: 'registered-run-artifact', source_kind: 'structure_artifact', format: 'mmcif', sha256: sha('f'), bytes: 8192,
        metadata: { name: 'Completed kinase fold / model 2', producer_backend: 'external_import', candidate_id: 'model-2', backend_coordinates: { backend: 'external_import', target_id: 'target-b', staged_index: 0 } },
        authority_receipt: {
            schema_name: 'cm_source_authority_receipt', schema_version: 1, source_id: 'registered-run-artifact',
            source_kind: 'structure_artifact', content_sha256: sha('f'), authority_kind: 'completed_run_artifact', receipt_sha256: sha('0'),
            payload: { request_id: 'run-1', job_id: 'job-1', artifact_id: 'artifact-b', candidate_id: 'model-2', content_sha256: sha('f'), backend_coordinates: { backend: 'external_import', target_id: 'target-b', staged_index: 0 } },
        },
    };
    const mounted = await mountLauncher({
        listSources: async () => [],
        listReusableRuns: async () => runs,
        registerRunArtifact: async (runId: string, artifactId: string) => {
            registrations.push([runId, artifactId]);
            return runSource;
        },
        submitRequest: async (payload: CmSubmitRequest) => {
            submissions.push(payload);
            return {
                request_id: 'request-import', job_id: 'request-import', status: 'queued',
                backend: payload.backend, request_sha256: sha('1'), coordinate_plan_sha256: sha('2'), expected_cardinality: 1,
            };
        },
    }, { backend: 'external_import', name: 'Reuse run' });

    await clickButton(mounted.renderer, /^Your Runs$/i);
    assert.match(text(mounted.renderer.root), /Completed kinase fold/);
    assert.match(text(mounted.renderer.root), /model-1/);
    assert.match(text(mounted.renderer.root), /model-2/);
    assert.equal(registrations.length, 0, 'opening a run must not silently choose an artifact');
    await clickButton(mounted.renderer, /Use model-2/i);
    assert.deepEqual(registrations, [['run-1', 'artifact-b']]);
    assert.match(text(mounted.renderer.root), new RegExp(`${sha('f')}.*target-b|target-b.*${sha('f')}`, 'i'));
    await clickButton(mounted.renderer, /Launch conformational mapping/i);
    assert.deepEqual(submissions[0]?.registered_artifact_ids, ['registered-run-artifact']);
    assert.equal(Object.hasOwn(submissions[0] || {}, 'registered_artifact_id'), false);

    const sourceKind = mounted.renderer.root.findAllByType('select').find((item) => {
        const options = item.findAllByType('option').map(text);
        return options.includes('Protein mmCIF upload');
    });
    assert.ok(sourceKind);
    assert.equal(sourceKind.findAllByType('option').some((option) => option.props.value === 'structure_artifact'), false,
        'an uploaded caller must not be allowed to self-declare prior-run authority');
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted selected-input preview and summary show only digest-bound receipt identity', async () => {
    const authoritativeSource: CmSource = {
        source_id: 'rcsb-authoritative',
        source_kind: 'structure_upload',
        format: 'mmcif',
        sha256: sha('9'),
        bytes: 4096,
        created_at: '2026-08-09T12:00:00Z',
        metadata: {
            name: 'ATTACKER SELECTED INPUT LABEL',
            target_id: 'attacker-target-id',
            provider: 'ATTACKER',
            accession: 'EVIL',
            model_id: 'attacker-model',
            sample_id: 'attacker-sample',
            chain_ids: ['Z'],
            entity_ids: ['999'],
            normalized_metadata: { accession: 'FAKE', chain_ids: ['Y'] },
        },
        authority_receipt: {
            schema_name: 'cm_source_authority_receipt',
            schema_version: 1,
            source_id: 'rcsb-authoritative',
            source_kind: 'structure_upload',
            content_sha256: sha('9'),
            authority_kind: 'rcsb_download',
            receipt_sha256: sha('8'),
            payload: {
                provider: 'RCSB',
                accession: '1UBQ',
                selection: {
                    accession: '1UBQ', model_id: '1', sample_id: 'asymmetric-unit',
                    chain_ids: ['A'], entity_ids: ['1'],
                },
            },
        },
    };
    const mismatchedSource: CmSource = {
        ...authoritativeSource,
        source_id: 'rcsb-mismatched',
        sha256: sha('7'),
        metadata: { name: 'MISMATCHED RECEIPT ATTACKER LABEL' },
        authority_receipt: {
            ...authoritativeSource.authority_receipt!,
            source_id: 'different-source-id',
            content_sha256: sha('7'),
            receipt_sha256: sha('6'),
        },
    };
    const mounted = await mountLauncher({ listSources: async () => [authoritativeSource, mismatchedSource] }, {
        backend: 'external_import',
        name: 'Authoritative preview',
        registered_artifact_ids: [authoritativeSource.source_id],
    });

    for (const tab of [/^RCSB$/i, /^Cached$/i]) {
        await clickButton(mounted.renderer, tab);
        const browser = mounted.renderer.root.findByProps({ 'aria-labelledby': 'cm-source-browser-heading' });
        const browserText = text(browser);
        assert.match(browserText, /rcsb-authoritative/);
        assert.match(browserText, /provider RCSB/i);
        assert.match(browserText, /accession 1UBQ/i);
        assert.match(browserText, /receipt 888888888888/i);
        assert.match(browserText, /registered 2026-08-09T12:00:00Z/i);
        assert.match(browserText, /available/i);
        assert.doesNotMatch(browserText, /ATTACKER SELECTED INPUT LABEL|attacker-target-id|provider ATTACKER|accession EVIL/i);
        assert.doesNotMatch(browserText, /rcsb-mismatched|MISMATCHED RECEIPT ATTACKER LABEL|receipt 666666666666/i);
        assert.doesNotMatch(browserText, /9999999999999999999999999999999999999999999999999999999999999999|structure_upload|4096 bytes/i);
    }

    const preview = mounted.renderer.root.findByProps({ 'aria-labelledby': 'cm-preview-heading' });
    const summary = mounted.renderer.root.findByProps({ 'aria-labelledby': 'cm-summary-heading' });
    const launcherText = text(mounted.renderer.root);
    assert.doesNotMatch(launcherText, /ATTACKER SELECTED INPUT LABEL|attacker-target-id/i);
    assert.match(launcherText, /rcsb-authoritative/);
    for (const surface of [preview, summary]) {
        const surfaceText = text(surface);
        assert.match(surfaceText, /rcsb-authoritative/);
        assert.match(surfaceText, /accession 1UBQ/i);
        assert.match(surfaceText, /model 1/i);
        assert.match(surfaceText, /sample asymmetric-unit/i);
        assert.match(surfaceText, /chains? A/i);
        assert.match(surfaceText, /entities? 1/i);
        assert.doesNotMatch(surfaceText, /ATTACKER SELECTED INPUT LABEL|attacker-target-id|ATTACKER|EVIL|FAKE|attacker-model|attacker-sample|chains? Z|chains? Y|entities? 999/i);
        assert.doesNotMatch(surfaceText, /Model, sample, and chain context resolve at server normalization/i);
    }

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted selected-input identity is unavailable when receipt binding mismatches despite attacker metadata', async () => {
    const taintedSource: CmSource = {
        source_id: 'rcsb-tainted', source_kind: 'structure_upload', format: 'mmcif', sha256: sha('4'), bytes: 4096,
        metadata: {
            name: 'Untrusted source label', provider: 'ATTACKER', accession: 'EVIL', model_id: 'browser-model',
            sample_id: 'browser-sample', chain_ids: ['Z'], entity_ids: ['999'],
            normalized_metadata: { provider: 'FAKE', accession: 'FAKE', chain_ids: ['Y'] },
        },
        authority_receipt: {
            schema_name: 'cm_source_authority_receipt', schema_version: 1,
            source_id: 'rcsb-tainted', source_kind: 'structure_upload', content_sha256: sha('5'),
            authority_kind: 'rcsb_download', receipt_sha256: sha('6'),
            payload: {
                provider: 'RCSB', accession: '1UBQ',
                selection: { accession: '1UBQ', model_id: '1', sample_id: 'asymmetric-unit', chain_ids: ['A'], entity_ids: ['1'] },
            },
        },
    };
    const mounted = await mountLauncher({ listSources: async () => [taintedSource] }, {
        backend: 'external_import', name: 'Fail-closed identity', registered_artifact_ids: [taintedSource.source_id],
    });

    const preview = mounted.renderer.root.findByProps({ 'aria-labelledby': 'cm-preview-heading' });
    const summary = mounted.renderer.root.findByProps({ 'aria-labelledby': 'cm-summary-heading' });
    for (const surface of [preview, summary]) {
        const surfaceText = text(surface);
        assert.match(surfaceText, /source identity unavailable|server-owned source identity unavailable/i);
        assert.doesNotMatch(surfaceText, /provider ATTACKER|accession EVIL|accession FAKE|model browser-model|sample browser-sample|chains? Z|chains? Y|entities? 999|accession 1UBQ/i);
    }

    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted RCSB source path supports keyword search, entry metadata, and explicit ambiguous context selection', async () => {
    const searches: string[] = [];
    const registrations: Array<Record<string, unknown>> = [];
    const response: CmRcsbSearchResponse = {
        query: 'kinase', total_count: 1,
        results: [{
            accession: '4HHB', title: 'Human deoxyhaemoglobin', method: 'X-RAY DIFFRACTION', resolution: 1.74,
            organism: 'Homo sapiens', release_date: '1984-07-17',
            models: [{ model_id: '1', label: 'Model 1' }, { model_id: '2', label: 'Model 2' }],
            samples: [{ sample_id: 'sample-a', label: 'Biological assembly 1' }],
            chains: [{ chain_id: 'A', label: 'Alpha chain', entity_id: '1', entity_type: 'protein', residue_count: 141 }, { chain_id: 'B', label: 'Beta chain', entity_id: '2', entity_type: 'protein', residue_count: 146 }],
            entities: [{ entity_id: '1', label: 'Hemoglobin alpha', entity_type: 'protein', residue_count: 141 }, { entity_id: '2', label: 'Hemoglobin beta', entity_type: 'protein', residue_count: 146 }],
            required_selection: ['model_id', 'sample_id', 'chain_ids', 'entity_ids'],
        }],
    };
    const mounted = await mountLauncher({
        listSources: async () => [],
        searchRcsb: async (query: string) => { searches.push(query); return response; },
        registerRcsb: async (selection: Record<string, unknown>) => {
            registrations.push(selection);
            return {
                source_id: 'rcsb-4hhb', source_kind: 'structure_upload', format: 'mmcif', sha256: sha('1'), bytes: 10000,
                metadata: { name: 'RCSB 4HHB' },
                authority_receipt: {
                    schema_name: 'cm_source_authority_receipt', schema_version: 1, source_id: 'rcsb-4hhb', source_kind: 'structure_upload',
                    content_sha256: sha('1'), authority_kind: 'rcsb_download', receipt_sha256: sha('2'),
                    payload: { provider: 'RCSB', accession: '4HHB', model_id: '2', sample_id: 'sample-a', chain_ids: ['B'], entity_ids: ['2'] },
                },
            } as CmSource;
        },
    }, { backend: 'external_import', name: 'RCSB import' });

    await clickButton(mounted.renderer, /^RCSB$/i);
    const searchInput = mounted.renderer.root.findAllByType('input').find((item) => item.props['aria-label'] === 'RCSB accession or keyword');
    assert.ok(searchInput);
    await act(async () => searchInput.props.onChange({ target: { value: 'kinase' } }));
    await clickButton(mounted.renderer, /Search RCSB/i);
    assert.deepEqual(searches, ['kinase']);
    assert.match(text(mounted.renderer.root), /Human deoxyhaemoglobin/);
    await clickButton(mounted.renderer, /Select 4HHB/i);
    assert.equal(registrations.length, 0, 'ambiguous provider entries must not register before explicit context selection');

    const modelSelect = mounted.renderer.root.findAllByType('select').find((item) => item.props['aria-label'] === 'RCSB model');
    const sampleSelect = mounted.renderer.root.findAllByType('select').find((item) => item.props['aria-label'] === 'RCSB sample');
    const chainSelect = mounted.renderer.root.findAllByType('select').find((item) => item.props['aria-label'] === 'RCSB chain');
    const entitySelect = mounted.renderer.root.findAllByType('select').find((item) => item.props['aria-label'] === 'RCSB entity');
    const registerButton = mounted.renderer.root.findAllByType('button').find((item) => /Register selected RCSB mmCIF/i.test(text(item)));
    assert.ok(modelSelect && sampleSelect && chainSelect && entitySelect && registerButton);
    assert.deepEqual(
        [modelSelect.props.value, sampleSelect.props.value, chainSelect.props.value, entitySelect.props.value],
        ['', '', '', ''],
        'every server-required RCSB context must start explicitly unresolved',
    );
    assert.equal(registerButton.props.disabled, true);

    await act(async () => modelSelect.props.onChange({ target: { value: '2' } }));
    assert.equal(registerButton.props.disabled, true, 'sample, chain, and entity remain unresolved');
    await act(async () => sampleSelect.props.onChange({ target: { value: 'sample-a' } }));
    assert.equal(registerButton.props.disabled, true, 'chain and entity remain unresolved');
    await act(async () => chainSelect.props.onChange({ target: { value: 'B' } }));
    assert.equal(entitySelect.props.value, '', 'choosing a chain must not silently choose its entity');
    assert.equal(registerButton.props.disabled, true, 'entity remains unresolved');
    await act(async () => entitySelect.props.onChange({ target: { value: '2' } }));
    assert.equal(registerButton.props.disabled, false);
    await clickButton(mounted.renderer, /Register selected RCSB mmCIF/i);
    assert.deepEqual(registrations, [{ accession: '4HHB', model_id: '2', sample_id: 'sample-a', chain_ids: ['B'], entity_ids: ['2'] }]);
    assert.match(text(mounted.renderer.root), /4HHB.*model 2.*sample-a.*chain B.*entity 2/i);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted RCSB search rejects incomplete server entries without rendering fabricated selectors', async () => {
    const originalAdapter = api.defaults.adapter;
    api.defaults.adapter = async (config) => ({
        data: {
            query: '4HHB', cached: false,
            entries: [{
                accession: '4HHB', title: 'Incomplete haemoglobin',
                chains: [], entities: [], required_selection: ['model_id'],
            }],
        },
        status: 200, statusText: 'OK', headers: {}, config,
    });
    const mounted = await mountLauncher({ listSources: async () => [] }, { backend: 'external_import', name: 'Reject incomplete RCSB' });
    try {
        await clickButton(mounted.renderer, /^RCSB$/i);
        const searchInput = mounted.renderer.root.findAllByType('input').find((item) => item.props['aria-label'] === 'RCSB accession or keyword');
        assert.ok(searchInput);
        await act(async () => searchInput.props.onChange({ target: { value: '4HHB' } }));
        await clickButton(mounted.renderer, /Search RCSB/i);
        assert.match(text(mounted.renderer.root), /RCSB search contract error/i);
        assert.doesNotMatch(text(mounted.renderer.root), /Incomplete haemoglobin|Model 1|Asymmetric unit/i);
        assert.equal(mounted.renderer.root.findAllByType('select').some((item) => item.props['aria-label'] === 'RCSB model'), false);
    } finally {
        await act(async () => mounted.renderer.unmount());
        mounted.client.clear();
        api.defaults.adapter = originalAdapter;
    }
});

const mountViewer = async (
    getStatus: () => Promise<CmStatus>,
    getFailureReceipts: () => Promise<CmFailureReceipt[]>,
    lifecycle: { cancelRequest?: () => Promise<unknown>; retryRequest?: () => Promise<unknown> } = {},
    includeLocationProbe = false,
) => {
    const queryClient = client();
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(
            <MemoryRouter><QueryClientProvider client={queryClient}>
                <ConformationalMappingViewer requestId="request-temporal" services={{
                    getStatus,
                    getProgress: async () => ({ request_id: 'request-temporal', status: 'running', progress: {}, job_stage: null, job_progress: null }),
                    getFailureReceipts,
                    getLogs: async () => ({ command_log: '', command_err: '', nextflow_log: '' }),
                    getResults: async () => { throw new Error('results should not load'); },
                    getLandscape: async () => { throw new Error('landscape should not load'); },
                    artifactUrl: () => '/unused',
                    cancelRequest: lifecycle.cancelRequest,
                    retryRequest: lifecycle.retryRequest,
                }} Workbench={() => <div />} />
                {includeLocationProbe && <LocationProbe />}
            </QueryClientProvider></MemoryRouter>,
        );
    });
    await flush();
    return { renderer: renderer!, client: queryClient };
};

test('mounted failure diagnostics refresh on running-to-failed transition and distinguish retrieval error from true empty', async () => {
    let lifecycleStatus: CmStatus['status'] = 'running';
    let receiptCalls = 0;
    const status = (): CmStatus => ({
        request_id: 'request-temporal', job_id: 'job-temporal', backend: 'protenix_v2_ensemble', status: lifecycleStatus,
        job_status: lifecycleStatus, progress: {}, failure_receipt: null, retry_eligible: false,
        result_contract_id: 'conformational_mapping_protenix_v1', run_record: null,
    });
    const mounted = await mountViewer(async () => status(), async () => {
        receiptCalls += 1;
        return lifecycleStatus === 'failed' ? [{ receipt_id: 'receipt-terminal', sha256: sha('3'), payload: { code: 'worker_failed' } }] : [];
    });
    assert.equal(receiptCalls, 1);
    lifecycleStatus = 'failed';
    await act(async () => { await mounted.client.invalidateQueries({ queryKey: ['cm-status', 'request-temporal'] }); });
    await flush();
    await clickButton(mounted.renderer, /Failure receipts/i);
    assert.equal(receiptCalls, 2, 'terminal status transition must refresh failure receipts');
    assert.match(text(mounted.renderer.root), /receipt-terminal/);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();

    const failed = await mountViewer(async () => status(), async () => { throw new Error('receipt store unavailable'); });
    await clickButton(failed.renderer, /Failure receipts/i);
    assert.match(text(failed.renderer.root), /Unable to retrieve failure receipts|receipt store unavailable/i);
    assert.doesNotMatch(text(failed.renderer.root), /No failure receipt is recorded/);
    await act(async () => failed.renderer.unmount());
    failed.client.clear();
});

test('mounted canonical viewer navigation and lifecycle controls use typed CM routes', async () => {
    const calls: string[] = [];
    let lifecycleStatus: CmStatus['status'] = 'running';
    const status = (): CmStatus => ({
        request_id: 'request-actions', job_id: 'job-actions', backend: 'protenix_v2_ensemble', status: lifecycleStatus,
        job_status: lifecycleStatus, progress: {}, failure_receipt: null, retry_eligible: lifecycleStatus === 'failed',
        result_contract_id: 'conformational_mapping_protenix_v1', run_record: null,
    });
    const mounted = await mountViewer(
        async () => status(), async () => [],
        { cancelRequest: async () => { calls.push('cancel:/api/conformational-mapping/requests/request-actions/cancel'); return {}; } },
        true,
    );
    const confirm = window.confirm;
    window.confirm = () => true;
    await clickButton(mounted.renderer, /Cancel request/i);
    assert.deepEqual(calls, ['cancel:/api/conformational-mapping/requests/request-actions/cancel']);
    await clickButton(mounted.renderer, /All results/i);
    assert.equal(mounted.renderer.root.findByProps({ 'data-mounted-location': '/designs' }).props['data-mounted-location'], '/designs');
    await clickButton(mounted.renderer, /New request/i);
    assert.equal(mounted.renderer.root.findByProps({ 'data-mounted-location': '/submit' }).props['data-mounted-location'], '/submit');
    window.confirm = confirm;
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();

    lifecycleStatus = 'failed';
    const retried = await mountViewer(
        async () => status(), async () => [],
        { retryRequest: async () => { calls.push('retry:/api/conformational-mapping/requests/request-actions/retry'); return {}; } },
    );
    await clickButton(retried.renderer, /Retry request/i);
    assert.deepEqual(calls.slice(-1), ['retry:/api/conformational-mapping/requests/request-actions/retry']);
    await act(async () => retried.renderer.unmount());
    retried.client.clear();
});

test('mounted JobSubmission catalog routes open the canonical and legacy CM launchers', async () => {
    const originalAdapter = api.defaults.adapter;
    api.defaults.adapter = async (config) => {
        const url = String(config.url || '');
        let data: unknown = [];
        if (url === '/api/models') data = [
            { id: 'conformational_mapping', name: 'Conformational Mapping', category: 'structure' },
            { id: 'confornets_experimental', name: 'ConforNets', category: 'structure' },
        ];
        else if (url === '/api/templates') data = [
            { id: 'conformational_mapping', name: 'Conformational Mapping', description: 'Canonical typed CM' },
            { id: 'confornets_experimental', name: 'ConforNets', description: 'Legacy CM entry' },
        ];
        else if (url.startsWith('/api/templates/')) {
            const id = url.split('/').at(-1)!;
            data = { id, name: id, preset_params: { template_model_id: id, template_mode_id: 'map' }, user_params: [] };
        } else if (url === '/api/conformational-mapping/sources') data = { sources: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    try {
        for (const templateId of ['conformational_mapping', 'confornets_experimental']) {
            const queryClient = client();
            let renderer: ReactTestRenderer | undefined;
            try {
                await act(async () => {
                    renderer = create(<MemoryRouter initialEntries={[`/submit?template=${templateId}`]}><QueryClientProvider client={queryClient}><JobSubmission /></QueryClientProvider></MemoryRouter>);
                });
                await flush();
                assert.equal(renderer!.root.findAllByProps({ 'data-bms-cm-launcher': 'canonical' }).length, 1, templateId);
            } finally {
                await act(async () => renderer?.unmount());
                queryClient.clear();
            }
        }
    } finally {
        api.defaults.adapter = originalAdapter;
    }
});

test('mounted /jobs/:id routes a CM job to the canonical viewer without generic cancellation', async () => {
    const originalFetch = globalThis.fetch;
    const originalAdapter = api.defaults.adapter;
    api.defaults.adapter = async () => { throw new Error('typed viewer data is intentionally unavailable in this routing test'); };
    globalThis.fetch = (async (input: RequestInfo | URL) => {
        if (String(input).endsWith('/api/jobs/cm-route')) {
            return new Response(JSON.stringify({
                id: 'cm-route', name: 'Canonical routed CM', model_id: 'conformational_mapping', mode: 'map',
                status: 'running', created_at: '2026-08-09T00:00:00Z', output_dir: '/unused',
            }), { status: 200, headers: { 'content-type': 'application/json' } });
        }
        return new Response('{}', { status: 404 });
    }) as typeof fetch;
    const queryClient = client();
    let renderer: ReactTestRenderer;
    try {
        await act(async () => {
            renderer = create(<MemoryRouter initialEntries={['/jobs/cm-route']}><QueryClientProvider client={queryClient}><Routes><Route path="/jobs/:jobId" element={<JobDetailPage />} /></Routes></QueryClientProvider></MemoryRouter>);
        });
        await flush();
        assert.equal(renderer!.root.findAllByProps({ 'data-bms-cm-viewer': 'canonical' }).length, 1);
        assert.equal(renderer!.root.findAllByType('button').some((button) => /Cancel request|Cancel this job/.test(text(button))), false);
    } finally {
        await act(async () => renderer?.unmount());
        queryClient.clear();
        api.defaults.adapter = originalAdapter;
        globalThis.fetch = originalFetch;
    }
});

test('mounted /designs/:id dispatches a CM job to the existing canonical viewer', async () => {
    const originalAdapter = api.defaults.adapter;
    const job = {
        id: 'cm-results-route',
        name: 'Canonical CM results',
        model_id: 'conformational_mapping',
        mode: 'map',
        status: 'running',
        created_at: '2026-08-09T00:00:00Z',
        output_dir: '/unused',
        params: {},
        design_count: 0,
    };
    api.defaults.adapter = async (config) => {
        const url = String(config.url || '');
        let data: unknown;
        if (url === '/api/jobs') data = { jobs: [job], total: 1 };
        else if (url === `/api/jobs/${job.id}`) data = job;
        else if (url === '/api/models/frustrampnn/integration') data = { model_id: 'frustrampnn', enabled: false };
        else if (url.endsWith(`/requests/${job.id}/status`)) data = {
            request_id: job.id,
            job_id: job.id,
            backend: 'protenix_v2_ensemble',
            status: 'running',
            job_status: 'running',
            progress: {},
            failure_receipt: null,
            retry_eligible: false,
            result_contract_id: 'conformational_mapping_protenix_v1',
            run_record: null,
        };
        else if (url.endsWith(`/requests/${job.id}/progress`)) data = {
            request_id: job.id, status: 'running', progress: {}, job_stage: null, job_progress: null,
        };
        else if (url.endsWith(`/requests/${job.id}/failures`)) data = { receipts: [] };
        else throw new Error(`unexpected mounted ResultsViewer request: ${url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    const queryClient = client();
    let renderer: ReactTestRenderer | undefined;
    try {
        await act(async () => {
            renderer = create(
                <MemoryRouter initialEntries={[`/designs/${job.id}`]}>
                    <QueryClientProvider client={queryClient}>
                        <Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>
                    </QueryClientProvider>
                </MemoryRouter>,
            );
        });
        await flush(12);
        assert.equal(renderer!.root.findAllByProps({ 'data-bms-cm-viewer': 'canonical' }).length, 1);
    } finally {
        await act(async () => renderer?.unmount());
        queryClient.clear();
        api.defaults.adapter = originalAdapter;
    }
});

test('typed RCSB search calls the CM authority endpoint with accession or keyword parameters', async () => {
    const originalAdapter = api.defaults.adapter;
    const requests: Array<{ url?: string; params?: unknown }> = [];
    api.defaults.adapter = async (config) => {
        requests.push({ url: config.url, params: config.params });
        return {
            data: { query: '4HHB', entries: [{
                accession: '4HHB', title: 'Haemoglobin',
                models: [{ model_id: '1', label: 'Model 1' }],
                samples: [{ sample_id: 'asymmetric-unit', label: 'Deposited asymmetric unit' }],
                chains: [{ chain_id: 'A', label: 'Author chain A', entity_id: '1', entity_type: 'protein', residue_count: 141 }],
                entities: [{ entity_id: '1', label: 'Protein entity 1', entity_type: 'protein', residue_count: 141 }],
                required_selection: ['model_id', 'sample_id', 'chain_ids', 'entity_ids'],
            }], cached: false },
            status: 200,
            statusText: 'OK',
            headers: {},
            config,
        };
    };
    try {
        const accession = await searchCmRcsb('4hhb');
        assert.equal(accession.results[0]?.accession, '4HHB');
        assert.deepEqual(requests[0], {
            url: '/api/conformational-mapping/sources/rcsb/search',
            params: { accession: '4HHB' },
        });
        await searchCmRcsb('kinase domain');
        assert.deepEqual(requests[1], {
            url: '/api/conformational-mapping/sources/rcsb/search',
            params: { keyword: 'kinase domain' },
        });
    } finally {
        api.defaults.adapter = originalAdapter;
    }
});

test('typed RCSB search rejects missing, reduced, and internally inconsistent selection authority', async () => {
    const originalAdapter = api.defaults.adapter;
    const valid = {
        accession: '4HHB', title: 'Haemoglobin',
        models: [{ model_id: '1', label: 'Model 1' }],
        samples: [{ sample_id: 'asymmetric-unit', label: 'Deposited asymmetric unit' }],
        chains: [{ chain_id: 'A', label: 'Author chain A', entity_id: '1', entity_type: 'protein', residue_count: 141 }],
        entities: [{ entity_id: '1', label: 'Protein entity 1', entity_type: 'protein', residue_count: 141 }],
        required_selection: ['model_id', 'sample_id', 'chain_ids', 'entity_ids'],
    };
    const malformed = [
        { ...valid, models: undefined },
        { ...valid, samples: [] },
        { ...valid, chains: [] },
        { ...valid, entities: undefined },
        { ...valid, required_selection: ['model_id'] },
        { ...valid, chains: [{ ...valid.chains[0], entity_id: 'missing' }] },
        { ...valid, chains: [{ ...valid.chains[0], residue_count: 140 }] },
    ];
    let index = 0;
    api.defaults.adapter = async (config) => ({
        data: { query: '4HHB', entries: [malformed[index++]], cached: false },
        status: 200, statusText: 'OK', headers: {}, config,
    });
    try {
        for (let attempt = 0; attempt < malformed.length; attempt += 1) {
            await assert.rejects(searchCmRcsb('4HHB'), /RCSB search contract error/i);
        }
    } finally {
        api.defaults.adapter = originalAdapter;
    }
});
