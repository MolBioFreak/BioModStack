import assert from 'node:assert/strict';
import test from 'node:test';

import type { StructureSceneState, ViewerSnapshot } from '../src/structureViewer/contracts/sceneState.js';
import {
    canonicalJson, createExportManifest, createViewerSnapshotV2, migrateViewerSnapshotV1,
    restoreViewerSnapshotV2, rowsToCsv, sha256Hex, validateExportManifest,
    type ViewerSnapshotBindingV2,
} from '../src/structureViewer/contracts/m6Reproducibility.js';
import {
    validateSpatialVolumeDescriptor, validateVolumePresentationState, validateVolumeSegmentation,
    type SpatialVolumeDescriptorV1, type VolumePresentationStateV1, type VolumeSegmentationV1,
} from '../src/structureViewer/contracts/spatialVolumes.js';
import {
    movieSemanticWarnings, validateMovieExportRequest, type AuthoritativeFrameStepper,
} from '../src/structureViewer/runtime/browserMovieExport.js';
import { viewerOk } from '../src/structureViewer/contracts/viewerResults.js';
import { StructureSceneController } from '../src/structureViewer/runtime/StructureSceneController.js';

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);
const DOC_ID = '11111111-1111-4111-8111-111111111111';
const VOLUME_ID = '44444444-4444-4444-8444-444444444444';

const scene = (): StructureSceneState => ({
    schemaVersion: 1,
    ref: { viewerId: 'viewer-1', sceneId: 'scene-1', generation: 4 },
    documents: [{ documentId: DOC_ID, sourceKind: 'pdb', sourceUrl: '/api/files/doc-1', contentSha256: HASH_A }],
    activeDocumentId: DOC_ID,
    provenance: { createdBy: 'test', createdAt: '2026-07-21T00:00:00.000Z', jobId: 'job-1' },
});
const bindings: readonly ViewerSnapshotBindingV2[] = [{ kind: 'document', resourceId: DOC_ID, sha256: HASH_A, required: true }];
const snapshot = () => createViewerSnapshotV2(scene(), {
    snapshotId: '22222222-2222-4222-8222-222222222222', capturedAt: '2026-07-21T00:00:01.000Z',
    adapterVersion: 'bms-direct:4.5.0', bindings, requiredCapabilities: ['snapshot-v2'],
    collectionState: null, comparisonState: null, volumeStates: [], uiComposition: 'standard',
    provenance: scene().provenance,
});

const volume = (overrides: Partial<SpatialVolumeDescriptorV1> = {}): SpatialVolumeDescriptorV1 => ({
    schemaVersion: 1, volumeId: VOLUME_ID,
    artifactId: '55555555-5555-4555-8555-555555555555', artifactSha256: HASH_A, byteLength: 49_152,
    format: 'ccp4', dimensions: [32, 24, 16], axisOrder: [0, 1, 2],
    gridToWorldRowMajor4x4: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    coordinateUnits: 'Å', valueUnits: 'e/Å³', semanticKind: 'density', channelCount: 1,
    statistics: { min: -3, max: 4, mean: 0, sigma: 1 },
    recommendedDisplay: { channel: 0, contourSigma: 1.5, opacity: 0.5 },
    registrationRef: null, provenanceRef: 'analysis:volume-fixture', ...overrides,
});
const presentation = (): VolumePresentationStateV1 => ({
    schema: 'bms.viewer.volume-presentation.v1', volumeId: VOLUME_ID, channel: 0, visible: true,
    representation: 'isosurface', contour: { mode: 'sigma', value: 1.5 }, opacity: 0.5, color: 0x38bdf8,
    slice: null, crop: null, visibleSegmentIds: [], registrationRef: null,
});

test('RFC 8785 canonical JSON and SHA-256 are stable and reject non-JSON values', async () => {
    assert.equal(canonicalJson({ z: 1e-7, a: -0 }), '{"a":0,"z":1e-7}');
    assert.equal(canonicalJson({ z: [3, null], a: { y: true, x: 'Å' } }), canonicalJson({ a: { x: 'Å', y: true }, z: [3, null] }));
    assert.match(await sha256Hex(canonicalJson({ a: 1 })), /^[0-9a-f]{64}$/);
    assert.throws(() => canonicalJson({ value: Number.NaN }));
    assert.throws(() => canonicalJson({ value: undefined }));
    assert.throws(() => canonicalJson({ value: '\ud800' }));
});

test('snapshot v2 strips transport, binds all documents, and restores only exact hashes', () => {
    const value = snapshot();
    assert.equal(value.scene.documents[0]?.sourceUrl, undefined);
    assert.equal(restoreViewerSnapshotV2(value, bindings).status, 'ok');
    assert.equal(restoreViewerSnapshotV2(value, [{ ...bindings[0]!, sha256: HASH_B }]).status, 'unsupported');
    assert.equal(restoreViewerSnapshotV2(value, []).status, 'unsupported');
});

test('snapshot v1 migration preserves only verified document bindings', () => {
    const legacy: ViewerSnapshot = { schemaVersion: 1, scene: scene(), adapterVersion: 'bms-direct:4.5.0', capturedAt: '2026-07-21T00:00:01.000Z', documentHashes: { [DOC_ID]: HASH_A } };
    const migrated = migrateViewerSnapshotV1(legacy, '33333333-3333-4333-8333-333333333333');
    assert.deepEqual(migrated.volumeStates, []);
    assert.equal(migrated.bindings[0]?.resourceId, DOC_ID);
    assert.throws(() => migrateViewerSnapshotV1({ ...legacy, scene: { ...legacy.scene, documents: [{ ...legacy.scene.documents[0]!, contentSha256: '' }] }, documentHashes: {} }, crypto.randomUUID()), /no SHA-256/i);
});

test('governed CSV/export manifest preserves missingness and exact source state', async () => {
    const csv = rowsToCsv([{ residue: 'A:1', score: 0 }, { residue: 'A:2', score: null }], ['residue', 'score']);
    assert.equal(csv, 'residue,score\r\nA:1,0\r\nA:2,\r\n');
    const manifest = await createExportManifest({
        exportId: crypto.randomUUID(), kind: 'table_csv', createdAt: '2026-07-21T00:00:02.000Z',
        workflowContext: { route: 'job-results' }, jobId: 'job-1', snapshot: snapshot(),
        exportParameters: { missingness: 'blank', scoreUnits: 'dimensionless' },
        output: new TextEncoder().encode(csv), outputFileName: 'residues.csv',
    });
    assert.equal(validateExportManifest(manifest).status, 'ok');
    assert.equal(manifest.snapshotId, snapshot().snapshotId);
});

test('volume contracts admit exact CCP4 descriptors and fail before allocation', () => {
    assert.equal(validateSpatialVolumeDescriptor(volume()).status, 'ok');
    assert.equal(validateSpatialVolumeDescriptor(volume({ dimensions: [4097, 1, 1] })).status, 'unsupported');
    assert.equal(validateSpatialVolumeDescriptor(volume({ artifactSha256: 'bad' })).status, 'unsupported');
    assert.equal(validateVolumePresentationState(presentation(), volume()).status, 'ok');
    assert.equal(validateVolumePresentationState({ ...presentation(), representation: 'slice', slice: { axis: 0, index: 100 } }, volume()).status, 'unsupported');
});

test('supplied segmentation labels require exact hash and an acyclic hierarchy', () => {
    const segmentation: VolumeSegmentationV1 = {
        schema: 'bms.viewer.volume-segmentation.v1', segmentationId: '66666666-6666-4666-8666-666666666666',
        volumeId: VOLUME_ID, artifactId: '77777777-7777-4777-8777-777777777777', artifactSha256: HASH_B,
        labels: [{ segmentId: 1, label: 'Domain A', parentSegmentId: null, recommendedColor: 0xf97316 }], provenanceRef: 'analysis:labels',
    };
    const labelVolume = volume({ semanticKind: 'segmentation', valueUnits: undefined });
    assert.equal(validateVolumeSegmentation(segmentation, labelVolume).status, 'ok');
    const cycle: VolumeSegmentationV1 = { ...segmentation, labels: [{ segmentId: 1, label: null, parentSegmentId: 2, recommendedColor: null }, { segmentId: 2, label: null, parentSegmentId: 1, recommendedColor: null }] };
    assert.equal(validateVolumeSegmentation(cycle, labelVolume).status, 'unsupported');
});

test('movie export remains gated until real VP9 proof and morph warning is permanent', () => {
    const stepper: AuthoritativeFrameStepper = {
        sourceKind: 'interpolated_morph', provenanceRef: 'morph:1', sourceBindings: bindings,
        frames: [{ kind: 'interpolated_morph', morphId: 'morph-1', morphStep: 0, semanticWarning: 'visual_interpolation_not_physical_trajectory' }],
        apply: async () => viewerOk(undefined),
    };
    assert.equal(validateMovieExportRequest({ fps: 30, bitrate: 2_000_000, outputFileName: 'morph.webm', codec: 'video/webm;codecs=vp9', sourceSnapshotSha256: HASH_A, capabilityProven: false }, stepper, { width: 800, height: 600 }).status, 'unsupported');
    assert.deepEqual(movieSemanticWarnings('interpolated_morph'), ['visual_interpolation_not_physical_trajectory']);
    assert.deepEqual(movieSemanticWarnings('coordinate_trajectory'), []);
});

test('scene controller owns governed volume lifecycle through the one adapter', async () => {
    const calls: string[] = [];
    let failNextScene = false;
    const adapter = {
        adapterId: 'bms-direct', adapterVersion: 'bms-direct:4.5.0',
        subscribeResidueClicks: () => () => undefined,
        diagnostics: () => ({ ownerGeneration: 1, ownerCreateCount: 1, ownerDisposeCount: 0, activeOwnerCount: 1, activeReactRootCount: 1, invariantViolations: [] }),
        reconcileScene: async () => { calls.push('scene'); if (failNextScene) { failNextScene = false; return { status: 'error', error: new Error('commit failed') }; } return viewerOk(undefined); }, clearScene: async () => viewerOk(undefined), dispose: async () => undefined,
        loadVolume: async () => { calls.push('volume'); return viewerOk(undefined); },
        setVolumePresentation: async () => { calls.push('presentation'); return viewerOk(undefined); },
        removeVolume: async () => { calls.push('remove'); return viewerOk(undefined); },
        applyVolumeRegistration: async () => viewerOk(undefined), capturePng: async () => viewerOk(new Blob()),
        exportSelectionMmcif: async () => viewerOk(new Blob()), getCanvasElement: () => ({ status: 'unsupported', reason: 'none', capabilityId: 'export-webm-v1' }),
    };
    const controller = new StructureSceneController(adapter as never);
    assert.equal((await controller.loadScene(scene())).status, 'ok');
    assert.equal((await controller.loadVolume(volume(), presentation())).status, 'ok');
    assert.equal((await controller.removeVolume(VOLUME_ID)).status, 'ok');
    assert.deepEqual(calls, ['scene', 'volume', 'presentation', 'remove']);
    const priorGeneration = controller.currentScene!.ref.generation;

    const nextScene = scene();
    const transactionalSnapshot = createViewerSnapshotV2({ ...nextScene, ref: { ...nextScene.ref, generation: 2 } }, {
        snapshotId: '33333333-3333-4333-8333-333333333333', capturedAt: '2026-07-22T00:00:00.000Z', adapterVersion: 'bms-direct:4.5.0',
        bindings, requiredCapabilities: [], collectionState: null, comparisonState: null, volumeStates: [], uiComposition: 'standard', provenance: nextScene.provenance,
    });
    failNextScene = true;
    const failedRestore = await controller.restoreSnapshotV2(transactionalSnapshot, bindings);
    assert.equal(failedRestore.status, 'error');
    assert.equal(controller.currentScene?.ref.generation, priorGeneration, 'failed restore must retain the previous authoritative scene');
    assert.equal((await controller.restoreSnapshotV2(transactionalSnapshot, [])).status, 'unsupported', 'missing required bindings must fail before adapter mutation');
    await controller.dispose();
});
