import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildResultsViewerMolecularDynamicsRoute, parseMolecularDynamicsHandoffRoute, buildMolecularDynamicsHandoffInitialValues } from '../src/components/gen2StartingStructureState';
import { hydrateMolecularDynamicsNative, serializeMolecularDynamicsNativeDraft, parseNativeMdp, renderNativeMdp, molecularDynamicsSourceKey, resolveMolecularDynamicsCloneSource, type MolecularDynamicsNativeIntent } from '../src/components/molecularDynamicsUiState';

const job = '11111111-1111-4111-8111-111111111111';
const source = { kind: 'candidate' as const, id: job, candidate_ref: { kind: 'native_document' as const, job_id: job, artifact_id: 'exact-nonprimary', target_state: 'unbound', model: 2 } };

for (const selected of [source, { kind: 'design', id: job, model_number: 2 }]) {
    for (const stale of [false, true]) {
        test(`v1 clone preserves ${selected.kind} exact selection, stale fallback=${stale}`, () => {
            const initial = { intent: { schema_version: 'bms.md.launch-intent.v1', source_ref: selected },
                ...(stale ? { source_design_id: '22222222-2222-4222-8222-222222222222' } : {}) };
            assert.deepEqual(resolveMolecularDynamicsCloneSource(JSON.parse(JSON.stringify(initial))), selected);
        });
    }
}

test('Job Clone binds exact source to its retained MD snapshot without changing the CandidateRef', () => {
    const snapshotJob = '33333333-3333-4333-8333-333333333333';
    const clone = resolveMolecularDynamicsCloneSource({ source_job_id: snapshotJob,
        md_source_provenance: { source_ref: source, snapshot: { structure: '/old/worker/selected.pdb' } } });
    assert.deepEqual(clone, { ...source, snapshot_job_id: snapshotJob });
    const route = buildResultsViewerMolecularDynamicsRoute(job, null, { sourceRef: clone!, returnTo: `/designs/${job}` });
    assert.deepEqual(parseMolecularDynamicsHandoffRoute(route.slice(route.indexOf('?'))).sourceRef, clone);
});

test('exact native document, member and safe parent survive sender and closed receiver without a Design', () => {
    const returnTo = `/designs/${job}?artifact_id=exact-nonprimary&model=2`;
    const href = buildResultsViewerMolecularDynamicsRoute(job, null, { sourceRef: source, returnTo });
    const route = parseMolecularDynamicsHandoffRoute(href.slice(href.indexOf('?')));
    assert.deepEqual(route.sourceRef, source);
    assert.equal(route.returnTo, returnTo);
    assert.equal(route.sourceDesignId, null);
    assert.deepEqual(buildMolecularDynamicsHandoffInitialValues(route, null).source_ref, source);
    for (const bad of ['https://outside.invalid', '//outside.invalid', '/\\outside.invalid']) {
        assert.throws(() => buildResultsViewerMolecularDynamicsRoute(job, null, { sourceRef: source, returnTo: bad }));
    }
    assert.notEqual(molecularDynamicsSourceKey(source), molecularDynamicsSourceKey({ ...source, candidate_ref: { ...source.candidate_ref, model: 1 } }));
});

test('cold native draft preserves zero, false, empty, unset, unknown spelling, windows, return and Local placement', () => {
    const mdp = parseNativeMdp('nsteps = 0\ngen-vel = no\nUnknown_New-Key = exact\ndefine =');
    const intent: MolecularDynamicsNativeIntent = { schema_version: 'bms.md.launch-intent.v2', name: 'selected',
        input: { kind: 'guided', source_ref: source, neutralize: false, salt_molar: 0 },
        return_to: `/designs/${job}`, execution_target_id: null, execution_policy: { remote_result_policy: 'automatic' },
        stages: [{ name: 'custom', mdp }], windows: [{ id: 'zero', mdp: { custom: { 'pull-coord1-init': 0 } } }],
        replicas: 1, random_seed: 42, execution: { ntmpi: 1, ntomp: 1, gpu_offload: 'none', pin: 'off' }, analysis: null };
    const restored = hydrateMolecularDynamicsNative(JSON.parse(JSON.stringify({ intent })));
    assert.deepEqual(serializeMolecularDynamicsNativeDraft(restored!), intent);
    assert.deepEqual(parseNativeMdp(renderNativeMdp(restored!.stages[0].mdp)), mdp);
    assert.equal(Object.hasOwn(restored!.stages[0].mdp, 'dt'), false);
    assert.equal(restored!.stages[0].mdp.define, '');
});
