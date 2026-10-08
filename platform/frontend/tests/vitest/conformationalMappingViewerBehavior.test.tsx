import assert from 'node:assert/strict';
import { test } from 'vitest';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';

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
) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const captured: Array<{ primary: string; overlays: Array<{ id: string; structureUrl: string }> }> = [];
    const Workbench = (props: { structureUrl: string; overlayStructures?: Array<{ id: string; structureUrl: string }> }) => {
        captured.push({ primary: props.structureUrl, overlays: props.overlayStructures || [] });
        return <div data-workbench="stub" data-primary={props.structureUrl} data-overlays={JSON.stringify(props.overlayStructures || [])} />;
    };
    const frustraCaptured: Array<{ jobId: string; invocationId?: string }> = [];
    const legacyLandscapeRequests: Array<{ candidateId: string; offset: number; limit: number }> = [];
    const FrustraWorkbench = (props: { job: { id: string }; preferredInvocationId?: string }) => {
        frustraCaptured.push({ jobId: props.job.id, invocationId: props.preferredInvocationId });
        return <div data-frustra-workbench="stub" data-job-id={props.job.id} data-invocation-id={props.preferredInvocationId} />;
    };
    const services = {
        getStatus: async () => ({
            request_id: 'request-viewer', status: 'completed', job_id: 'retry-job', job_status: 'completed',
            result_contract_id: backend === 'confornets' ? 'conformational_mapping_confornets_v1' : 'conformational_mapping_protenix_v1', retry_eligible: false,
            progress: { phase: 'completed', completed_coordinates: candidateCount, expected_coordinates: candidateCount },
            failure_receipt: null,
        } as never),
        getProgress: async () => ({ progress: { phase: 'completed', completed_coordinates: candidateCount, expected_coordinates: candidateCount } } as never),
        getFailureReceipts: async () => [],
        getResults: async () => results(candidateCount, backend, frustraDataShape),
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
            <MemoryRouter><QueryClientProvider client={client}>
                <ConformationalMappingViewer requestId="request-viewer" services={services as never} Workbench={Workbench as never} FrustraWorkbench={FrustraWorkbench as never} />
            </QueryClientProvider></MemoryRouter>,
        );
    });
    await flush();
    return { renderer: renderer!, client, captured, frustraCaptured, legacyLandscapeRequests };
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
