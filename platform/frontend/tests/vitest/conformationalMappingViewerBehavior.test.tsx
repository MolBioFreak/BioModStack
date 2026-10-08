import assert from 'node:assert/strict';
import { test } from 'vitest';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';

import { ConformationalMappingViewer } from '../../src/components/conformationalMapping/ConformationalMappingViewer.js';
import type { CmResults } from '../../src/components/conformationalMapping/conformationalMappingApi.js';
import { CANONICAL_AMINO_ACIDS } from '../../src/components/conformationalMapping/conformationalMappingSemantics.js';

const sha = (letter: string) => letter.repeat(64);

type ProducerBackend = 'protenix_v2_ensemble' | 'confornets';
type FrustraDataShape = 'global' | 'legacy' | 'legacy_mixed' | 'legacy_page_extra' | 'legacy_refetch_error';

const results = (
    count: number,
    backend: ProducerBackend = 'protenix_v2_ensemble',
    frustraDataShape: FrustraDataShape = 'global',
): CmResults => {
    const coordinates = Array.from({ length: count }, (_, index) => backend === 'confornets' ? ({
        backend, target_id: 'target-a', task: 'diversity', test_case_id: 'target-a', reference_id: null,
        run_index: 0, saved_step: 0, confornet_index: index, sample_index: 0,
    }) : ({
        backend, target_id: 'target-a', ordered_seed: 101 + index, sample_index: index,
    }));
    const candidates = coordinates.map((backend_coordinates, index) => ({
        candidate_id: `candidate-${index + 1}`,
        backend_coordinates,
        authoritative_structure_path: `native/target-a/structure-${index + 1}.cif`,
        authoritative_structure_sha256: sha(String(index + 1)),
        sidecar_paths: [`native/target-a/confidence-${index + 1}.json`, `native/target-a/full-data-${index + 1}.json`],
    }));
    return {
        request_id: 'request-viewer', backend, status: 'completed',
        result_contract_id: backend === 'confornets' ? 'conformational_mapping_confornets_v1' : 'conformational_mapping_protenix_v1',
        records: [{
            type: 'ensemble', key: 'primary', sha256: sha('a'), payload: {
                schema_name: 'cm_ensemble', schema_version: 1, request_id: 'request-viewer', request_sha256: sha('b'),
                source_snapshot_sha256: sha('c'), backend, runtime_identity: 'runtime',
                container_digest: `sha256:${sha('d')}`, checkpoint_sha256: sha('e'), feature_policy_sha256: sha('f'),
                expected_cardinality: count, expected_coordinates: coordinates, candidates,
                native_manifest_path: 'cm_native_artifacts_v1.json', native_manifest_sha256: sha('0'), warnings: [], omissions: [],
                terminal_status: 'complete', started_at: '2026-07-31T00:00:00Z', completed_at: '2026-07-31T00:01:00Z',
                resumable: true, resume_key: sha('9'),
            },
        }, {
            type: 'analysis', key: 'primary', sha256: sha('8'), payload: {
                schema_name: 'cm_analysis', schema_version: 1, formula_version: 'cm_analysis_v1',
                results: [{
                    source_row_key: 'target-a:A:1', status: 'robust',
                    identity: { target_id: 'target-a', entity_instance_id: 'target-a:A', auth_asym_id: 'A', auth_seq_id: 1, sequence_index: 1 },
                    expected_coordinate_count: Math.max(1, count), valid_coordinate_count: count,
                    components: {}, sort_keys: {},
                }],
                expected_strata: ['primary'], support_records: [], pair_ledger: [], exclusions: [], clash_records: [],
                ranking_policy: { name: 'test_fixture' },
            },
        }, ...(frustraDataShape === 'global' ? [{
            type: 'frustrampnn_result_references', key: 'primary', sha256: sha('4'), payload: {
                schema_name: 'cm_frustrampnn_result_references', schema_version: 1,
            },
        }] : [{
            type: 'landscape', key: 'candidate-1', sha256: sha('4'), payload: {
                schema_name: 'cm_frustration_landscape', schema_version: 1,
            },
        }]), ...candidates.map((candidate, index) => ({
            type: 'structure_map', key: candidate.candidate_id, sha256: sha('7'), payload: {
                schema_name: 'cm_structure_map', schema_version: 1, candidate_id: candidate.candidate_id,
                original_cif_sha256: candidate.authoritative_structure_sha256, source_sha256: sha('6'), normalized_pdb_sha256: sha('5'),
                source_format: 'mmcif', normalizer_version: 'test-fixture-v1', altloc_policy: 'highest_occupancy',
                rows: [{
                    entity_instance_id: `target-a:A:${index + 1}`, label_asym_id: 'A', auth_asym_id: 'A',
                    sequence_index: 1, status: 'mapped',
                }],
            },
        }))],
        artifacts: candidates.map((candidate, index) => ({
            artifact_id: `artifact-${index + 1}`, candidate_id: candidate.candidate_id, role: 'authoritative_cif',
            relative_path: candidate.authoritative_structure_path, sha256: candidate.authoritative_structure_sha256,
            bytes: 20, media_type: 'chemical/x-mmcif',
        })),
    };
};

const text = (node: ReactTestInstance): string => node.children.map((child) => typeof child === 'string' ? child : text(child)).join('');
const flush = async () => { for (let index = 0; index < 8; index += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); }); };

const mount = async (
    candidateCount: number,
    backend: ProducerBackend = 'protenix_v2_ensemble',
    frustraDataShape: FrustraDataShape = 'global',
    search = '',
    unavailable: boolean | 'contradictory-candidate' = false,
) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const captured: Array<{ primary: string; overlays: Array<{ id: string; structureUrl: string }> }> = [];
    const Workbench = (props: { structureUrl: string; overlayStructures?: Array<{ id: string; structureUrl: string }> }) => {
        captured.push({ primary: props.structureUrl, overlays: props.overlayStructures || [] });
        return <div data-workbench="stub" data-primary={props.structureUrl} data-overlays={JSON.stringify(props.overlayStructures || [])} />;
    };
    const frustraCaptured: Array<{ jobId: string; invocationId?: string }> = [];
    let locationSearch = '';
    let locationPath = '';
    let go: ReturnType<typeof useNavigate>;
    const LocationProbe = () => { const location = useLocation(); locationSearch = location.search; locationPath = location.pathname; go = useNavigate(); return null; };
    const legacyLandscapeRequests: Array<{ candidateId: string; offset: number; limit: number }> = [];
    const FrustraWorkbench = (props: { job: { id: string }; preferredInvocationId?: string; scope?: string; onScopeChange?: (scope: string) => void; onOpenJob: (id: string) => void }) => {
        frustraCaptured.push({ jobId: props.job.id, invocationId: props.preferredInvocationId });
        return <div data-frustra-workbench="stub" data-job-id={props.job.id} data-invocation-id={props.preferredInvocationId} data-scope={props.scope}><button onClick={() => props.onScopeChange?.('whole-experiment')}>Test whole experiment</button><button onClick={() => props.onOpenJob('child-job')}>Test child route</button></div>;
    };
    const services = {
        getStatus: async () => ({
            request_id: 'request-viewer', backend, status: 'completed', job_id: 'retry-job', job_status: 'completed',
            result_contract_id: backend === 'confornets' ? 'conformational_mapping_confornets_v1' : 'conformational_mapping_protenix_v1', retry_eligible: false,
            progress: { phase: 'completed', completed_coordinates: candidateCount, expected_coordinates: candidateCount },
            failure_receipt: null,
        } as never),
        getProgress: async () => ({ progress: { phase: 'completed', completed_coordinates: candidateCount, expected_coordinates: candidateCount } } as never),
        getFailureReceipts: async () => [],
        getResults: async () => {
            if (unavailable === true) throw new Error('ancillary artifact unavailable');
            const value = results(candidateCount, backend, frustraDataShape);
            if (unavailable === 'contradictory-candidate') value.artifacts[0].sha256 = sha('f');
            return value;
        },
        getStateAnalysis: async () => {
            if (!unavailable) throw new Error('no normalized state projection in fixture');
            return {
                request_id: 'request-viewer', analysis_id: `cm_state_landscape_analysis_${'a'.repeat(32)}`,
                authority: { content_sha256: sha('a'), source_ensemble_sha256: sha('b'), source_landscape_sha256: sha('c'), source_structure_map_sha256: sha('d'), comparison_sha256: sha('e'), formula_version: 'cm_state_landscape_analysis_v1', formula_sha256: sha('f'), policy_sha256: sha('1') },
                comparison: { mode: 'pairwise', target_id: 'target-a', scope: 'all_within_target', reference_backend_coordinates: null, reference_candidate_id: null },
                counts: { pairs: 1, rows: 0, exclusions: 0 }, pairs: [{ pair_id: 'candidate-1__candidate-2', candidate_a_id: 'candidate-1', candidate_b_id: 'candidate-2' }], artifact: null,
            };
        },
        getStateAnalysisRows: async () => ({
            request_id: 'request-viewer', selected_analysis_id: `cm_state_landscape_analysis_${'a'.repeat(32)}`, offset: 0, limit: 50,
            applied_filters: { pair_id: 'candidate-1__candidate-2', candidate_id: null, entity_instance_id: null, auth_asym_id: null, sequence_start: null, sequence_end: null }, next_offset: null, rows: [],
        }),
        getLandscape: async (_requestId: string, candidateId: string, offset: number, limit: number) => {
            legacyLandscapeRequests.push({ candidateId, offset, limit });
            if (frustraDataShape === 'legacy_refetch_error' && legacyLandscapeRequests.length > 1) {
                throw new Error('landscape refetch failed');
            }
            return {
                ...(frustraDataShape === 'legacy_page_extra' ? { unexpected_page_field: 'reject-me' } : {}),
                request_id: 'request-viewer', candidate_id: candidateId, entity_instance_id: null,
                sequence_start: null, sequence_end: null, offset, limit, next_offset: null,
                rows: CANONICAL_AMINO_ACIDS.map((mutation_aa, index) => ({
                    candidate_id: frustraDataShape === 'legacy_mixed' && index === 1 ? 'candidate-foreign' : candidateId,
                    entity_instance_id: 'target-a:A', auth_asym_id: 'A', auth_seq_id: '1',
                    insertion_code: '', sequence_index: 1, wt: 'G', mutation_aa,
                    score: index === 0 ? -1.2 : index === 19 ? 0.7 : 0,
                    class: index === 0 ? 'high' : index === 19 ? 'minimally_frustrated' : 'neutral',
                    scoreable: true, status: 'ok', reason: null,
                    provenance: {
                        raw_csv_sha256: sha('1'),
                        checkpoint_sha256: sha('2'),
                        tool_sha256: sha('3'),
                        threshold_policy_sha256: sha('4'),
                    },
                })),
            } as never;
        },
        artifactUrl: (requestId: string, artifactId: string) => `/api/conformational-mapping/requests/${requestId}/artifacts/${artifactId}`,
    };
    let renderer: ReactTestRenderer;
    await act(async () => {
        renderer = create(
            <MemoryRouter initialEntries={[`/designs/retry-job${search}`]}><LocationProbe /><QueryClientProvider client={client}>
                <ConformationalMappingViewer requestId="request-viewer" services={services as never} Workbench={Workbench as never} FrustraWorkbench={FrustraWorkbench as never} />
            </QueryClientProvider></MemoryRouter>,
        );
    });
    await flush();
    return { renderer: renderer!, client, captured, frustraCaptured, legacyLandscapeRequests, search: () => locationSearch, pathname: () => locationPath, go: (to: number | string) => go(to as never) };
};

test('mounted viewer manages governed alternative overlays across candidate cardinalities', async () => {
    const two = await mount(2);
    const initialWorkbenches = two.renderer.root.findAllByProps({ 'data-workbench': 'stub' });
    if (initialWorkbenches.length === 0) {
        const alerts = two.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ');
        throw new Error(`viewer did not render workbench: ${alerts}`);
    }
    let workbench = initialWorkbenches[0];
    assert.equal(workbench.props['data-primary'], '/api/conformational-mapping/requests/request-viewer/artifacts/artifact-1');
    assert.deepEqual(JSON.parse(workbench.props['data-overlays']).map((item: { id: string }) => item.id), ['candidate-2']);
    assert.match(JSON.stringify(JSON.parse(workbench.props['data-overlays'])), /\/api\/conformational-mapping\/requests\/request-viewer\/artifacts\/artifact-2/u);

    const progressLens = two.renderer.root.findAllByType('button').find((node) => text(node) === 'Progress');
    const logsLens = two.renderer.root.findAllByType('button').find((node) => text(node) === 'Logs');
    const ensembleLens = two.renderer.root.findAllByType('button').find((node) => text(node) === 'Ensemble');
    assert.equal(progressLens?.props['aria-pressed'], true);
    assert.equal(logsLens?.props['aria-pressed'], false);
    assert.equal(ensembleLens?.props['aria-pressed'], true);
    await act(async () => logsLens?.props.onClick());
    assert.equal(two.renderer.root.findAllByType('button').find((node) => text(node) === 'Logs')?.props['aria-pressed'], true);

    const checkedAlternative = two.renderer.root.findAllByType('input').find((node) => node.props.checked === true);
    assert.ok(checkedAlternative);
    await act(async () => checkedAlternative.props.onChange({ target: { checked: false } }));
    await flush();
    workbench = two.renderer.root.findByProps({ 'data-workbench': 'stub' });
    assert.deepEqual(JSON.parse(workbench.props['data-overlays']), []);

    const candidateTwoButton = two.renderer.root.findAllByType('button').find((node) => text(node).includes('Candidate 2'));
    assert.ok(candidateTwoButton);
    await act(async () => candidateTwoButton.props.onClick());
    await flush();
    workbench = two.renderer.root.findByProps({ 'data-workbench': 'stub' });
    assert.equal(workbench.props['data-primary'], '/api/conformational-mapping/requests/request-viewer/artifacts/artifact-2');
    assert.deepEqual(JSON.parse(workbench.props['data-overlays']).map((item: { id: string }) => item.id), ['candidate-1']);
    await act(async () => two.renderer.unmount());
    two.client.clear();

    const one = await mount(1);
    workbench = one.renderer.root.findByProps({ 'data-workbench': 'stub' });
    assert.equal(workbench.props['data-primary'], '/api/conformational-mapping/requests/request-viewer/artifacts/artifact-1');
    assert.deepEqual(JSON.parse(workbench.props['data-overlays']), []);
    await act(async () => one.renderer.unmount());
    one.client.clear();

    const zero = await mount(0);
    assert.equal(zero.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 0);
    const alerts = zero.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ');
    assert.match(alerts, /validation failed closed|candidate|cardinality/i);
    await act(async () => zero.renderer.unmount());
    zero.client.clear();
});

test('mounted CM result keeps ConforNets and global FrustraMPNN data in sibling model views', async () => {
    const mounted = await mount(3, 'confornets');
    const candidateThree = mounted.renderer.root.findAllByType('button').find((node) => text(node).includes('Candidate 3'));
    assert.ok(candidateThree);
    await act(async () => candidateThree.props.onClick());
    const confornetsView = mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'ConforNets data');
    const frustraView = mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'FrustraMPNN data');
    assert.equal(confornetsView?.props['aria-pressed'], true);
    assert.equal(frustraView?.props['aria-pressed'], false);
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 1);
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-frustra-workbench': 'stub' }).length, 0);
    await act(async () => frustraView?.props.onClick());
    await flush();
    const workbench = mounted.renderer.root.findByProps({ 'data-frustra-workbench': 'stub' });
    assert.equal(workbench.props['data-job-id'], 'retry-job');
    assert.equal(
        workbench.props['data-invocation-id'],
        'frustrampnn:retry-job:candidate-3',
    );
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 0);
    assert.equal(mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'ConforNets data')?.props['aria-pressed'], false);
    assert.equal(mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'FrustraMPNN data')?.props['aria-pressed'], true);
    await act(async () => mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'ConforNets data')?.props.onClick());
    await flush();
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 1);
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-frustra-workbench': 'stub' }).length, 0);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted historical CM result keeps a bounded FrustraMPNN model view without global references', async () => {
    const mounted = await mount(1, 'confornets', 'legacy');
    const frustraView = mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'FrustraMPNN data');
    assert.ok(frustraView);

    await act(async () => frustraView.props.onClick());
    await flush();

    assert.match(text(mounted.renderer.root), /Persisted exact-20 FrustraMPNN landscape/i);
    assert.deepEqual(mounted.legacyLandscapeRequests, [{ candidateId: 'candidate-1', offset: 0, limit: 1000 }]);
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-frustra-workbench': 'stub' }).length, 0);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted historical FrustraMPNN view fails closed on mixed-candidate rows', async () => {
    const mounted = await mount(1, 'confornets', 'legacy_mixed');
    const frustraView = mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'FrustraMPNN data');
    assert.ok(frustraView);

    await act(async () => frustraView.props.onClick());
    await flush();

    const alerts = mounted.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ');
    assert.match(alerts, /selected candidate/i);
    assert.equal(mounted.renderer.root.findAllByType('table').length, 0);
    assert.doesNotMatch(text(mounted.renderer.root), /Landscape provenance identity/i);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted historical FrustraMPNN view rejects an unexpected page-envelope field', async () => {
    const mounted = await mount(1, 'confornets', 'legacy_page_extra');
    const frustraView = mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'FrustraMPNN data');
    assert.ok(frustraView);

    await act(async () => frustraView.props.onClick());
    await flush();

    const alerts = mounted.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ');
    assert.match(alerts, /exact keys|page envelope/i);
    assert.equal(mounted.renderer.root.findAllByType('table').length, 0);
    assert.doesNotMatch(text(mounted.renderer.root), /Landscape provenance identity/i);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});

test('mounted historical FrustraMPNN view suppresses retained data after a failed refetch', async () => {
    const mounted = await mount(1, 'confornets', 'legacy_refetch_error');
    const frustraView = mounted.renderer.root.findAllByType('button').find((node) => text(node) === 'FrustraMPNN data');
    assert.ok(frustraView);

    await act(async () => frustraView.props.onClick());
    await flush();
    assert.equal(mounted.renderer.root.findAllByType('table').length, 1);
    assert.match(text(mounted.renderer.root), /Landscape provenance identity/i);

    await act(async () => {
        await mounted.client.invalidateQueries({
            queryKey: ['cm-legacy-frustrampnn-landscape', 'request-viewer', 'candidate-1', 0],
        });
    });
    await flush();

    const alerts = mounted.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' ');
    assert.match(alerts, /landscape refetch failed|unavailable/i);
    assert.equal(mounted.renderer.root.findAllByType('table').length, 0);
    assert.doesNotMatch(text(mounted.renderer.root), /Landscape provenance identity/i);
    await act(async () => mounted.renderer.unmount());
    mounted.client.clear();
});


test('CM exact URL invocation, candidate handoff, experiment scope, history and canonical child context', async () => {
    const context = '&workspace_id=p&global_experiment_id=g&domain_experiment_id=d&global_experiment_revision_id=gr&domain_revision_id=dr';
    const mounted = await mount(3, 'confornets', 'global', '?frustrampnn_invocation_id=frustrampnn:retry-job:candidate-2' + context);
    const click = async (label: string) => { await act(async () => mounted.renderer.root.findAllByType('button').find(n => text(n) === label)!.props.onClick()); await flush(); };
    assert.equal(mounted.renderer.root.findByProps({ 'data-frustra-workbench': 'stub' }).props['data-invocation-id'], 'frustrampnn:retry-job:candidate-2');
    await click('Test whole experiment');
    assert.match(mounted.search(), /frustrampnn_scope=whole-experiment/);
    await click('ConforNets data');
    assert.match(mounted.renderer.root.findByProps({ 'data-workbench': 'stub' }).props['data-primary'], /artifact-2$/);
    await act(async () => mounted.renderer.root.findAllByType('button').find(n => text(n).includes('Candidate 3'))!.props.onClick());
    await click('FrustraMPNN data');
    assert.equal(new URLSearchParams(mounted.search()).get('frustrampnn_invocation_id'), 'frustrampnn:retry-job:candidate-3');
    await act(async () => mounted.go(-1)); await flush();
    assert.equal(new URLSearchParams(mounted.search()).get('cm_candidate_id'), 'candidate-3');
    await act(async () => mounted.go(1)); await flush();
    await click('Test child route');
    assert.equal(mounted.pathname(), '/designs/child-job');
    assert.match(mounted.search(), /global_experiment_revision_id=gr/);
    assert.equal(new URLSearchParams(mounted.search()).has('frustrampnn_invocation_id'), false);
    await act(async () => mounted.renderer.unmount()); mounted.client.clear();
});

test('CM exact native workbench remains addressable when the ancillary shell request fails', async () => {
    const mounted = await mount(2, 'confornets', 'global', '?result_model=frustrampnn&frustrampnn_invocation_id=frustrampnn:retry-job:candidate-2', true);
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-frustra-workbench': 'stub' }).length, 1);
    assert.match(mounted.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' '), /ancillary artifact unavailable/);
    assert.match(text(mounted.renderer.root), /State-landscape|State landscape|State analysis/i);
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 0);
    await act(async () => mounted.renderer.unmount()); mounted.client.clear();
});

test('CM unavailable exact candidate never substitutes the first candidate', async () => {
    const mounted = await mount(2, 'confornets', 'global', '?result_model=confornets&cm_candidate_id=missing');
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 0);
    assert.match(mounted.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' '), /exact requested candidate is unavailable/);
    await act(async () => mounted.renderer.unmount()); mounted.client.clear();
});


test('CM contradictory selected candidate digest fails closed without substituting coordinates', async () => {
    const mounted = await mount(2, 'confornets', 'global', '?cm_candidate_id=candidate-1', 'contradictory-candidate');
    assert.equal(mounted.renderer.root.findAllByProps({ 'data-workbench': 'stub' }).length, 0);
    assert.match(mounted.renderer.root.findAllByProps({ role: 'alert' }).map(text).join(' '), /validation failed closed/);
    await act(async () => mounted.renderer.unmount()); mounted.client.clear();
});
