import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

import type { CmLandscapePage, CmLandscapeRow } from '../src/components/conformationalMapping/conformationalMappingApi.js';
import {
    collectCompleteFrustraMpnnLandscape,
    createFrustraMpnnViewerMetrics,
    resolveFrustraMpnnResidueProfile,
} from '../src/components/conformationalMapping/frustraMpnnViewerMetrics.js';
import type { CmLandscapeResidue, CmStructureMap } from '../src/components/conformationalMapping/conformationalMappingSemantics.js';
import { CANONICAL_AMINO_ACIDS } from '../src/components/conformationalMapping/conformationalMappingSemantics.js';
import { MetricLegendPanel } from '../src/structureViewer/extensions/metrics/MetricLegendPanel.js';
import { MetricRegistry } from '../src/structureViewer/metrics/MetricRegistry.js';
import { projectResidueMetricLayer } from '../src/structureViewer/metrics/metricProjection.js';

const sha = (value: string): string => value.repeat(64);
const provenance = {
    checkpoint_id: 'megascale.ckpt', checkpoint_sha256: sha('a'),
    tool_id: 'frustrampnn', tool_sha256: sha('b'),
    container_sha256: sha('e'),
    threshold_policy_id: 'frustrampnn_class_v1', threshold_policy_sha256: sha('c'),
    raw_csv_sha256: sha('d'),
};

const residue = (overrides: Partial<CmLandscapeResidue> = {}): CmLandscapeResidue => ({
    key: 'copy-1:7', entity_instance_id: 'copy-1', auth_asym_id: 'AUTH', auth_seq_id: '42',
    insertion_code: 'A', sequence_index: 7, wt: 'G',
    slots: CANONICAL_AMINO_ACIDS.map((mutation_aa, index): CmLandscapeRow => ({
        candidate_id: 'candidate-1', entity_instance_id: 'copy-1', auth_asym_id: 'AUTH', auth_seq_id: '42',
        insertion_code: 'A', sequence_index: 7, wt: 'G', mutation_aa,
        score: mutation_aa === 'G' ? -1.25 : index / 10 - 0.8,
        class: mutation_aa === 'G' ? 'high' : index < 4 ? 'high' : index > 13 ? 'minimally_frustrated' : 'neutral',
        scoreable: true, status: 'ok', reason: null, provenance,
    })),
    ...overrides,
});

const structureMap = (overrides: Partial<CmStructureMap['rows'][number]> = {}): CmStructureMap => ({
    target_id: 'target-1', candidate_id: 'candidate-1', original_cif_sha256: sha('e'), source_format: 'mmcif',
    source_sha256: sha('f'), source_bytes: 123, normalized_pdb_sha256: sha('1'), selected_source_model: 1,
    altloc_policy: 'highest_occupancy', normalizer_version: 'v1',
    rows: [{
        entity_instance_id: 'copy-1', source_entity_id: '1', source_model: 1,
        label_asym_id: 'A', auth_asym_id: 'AUTH', label_seq_id: 7, auth_seq_id: 42,
        insertion_code: 'A', residue_name: 'GLY', sequence_index: 7, pdb_chain_id: 'A', pdb_residue_id: 7,
        pdb_insertion_code: '', backbone_atoms: {}, selected_altloc: '', model_decision: 'selected', status: 'mapped', reason: null,
        ...overrides,
    }],
});

test('canonical FrustraMPNN metrics preserve exact structure-map and author residue identity', () => {
    const bundle = createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [residue()], structureMap: structureMap(),
    });

    assert.deepEqual(bundle.layers.map((layer) => layer.descriptor.id), [
        'frustrampnn-native-index',
        'frustrampnn-high-substitution-fraction',
        'frustrampnn-maximum-substitution-delta',
    ]);
    const native = bundle.layers[0];
    assert.equal(native.descriptor.dimension, 'residue-scalar');
    assert.deepEqual(native.values[0]?.identity, {
        documentId: 'primary', entityId: '1', labelAsymId: 'A', authAsymId: 'AUTH',
        labelSeqId: 7, authSeqId: 42, insertionCode: 'A', sourceInstanceId: 'copy-1',
    });
    assert.equal(native.values[0]?.value, -1.25);
    assert.equal(native.descriptor.provenance.artifactSha256, sha('d'));
    assert.equal(native.descriptor.provenance.parameters?.container_sha256, sha('e'));
    assert.match(native.descriptor.semantics ?? '', /backbone-context model score/i);
});

test('FrustraMPNN container provenance mismatch fails closed', () => {
    const mismatched = residue();
    mismatched.slots[1] = {
        ...mismatched.slots[1],
        provenance: { ...mismatched.slots[1].provenance, container_sha256: sha('9') },
    };
    assert.throws(() => createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [mismatched], structureMap: structureMap(),
    }), /provenance mismatch for container_sha256/);
});

test('derived FrustraMPNN layers state formulas and exclude the native slot', () => {
    const bundle = createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [residue()], structureMap: structureMap(),
    });
    const highFraction = bundle.layers[1];
    const maximumDelta = bundle.layers[2];
    const nonNative = residue().slots.filter((slot) => slot.mutation_aa !== 'G');
    const expectedFraction = nonNative.filter((slot) => slot.class === 'high').length / nonNative.length;
    const expectedDelta = Math.max(...nonNative.map((slot) => slot.score as number)) - (-1.25);

    assert.equal(highFraction.values[0]?.value, expectedFraction);
    assert.equal(highFraction.descriptor.formula, 'count(non-native class = high) / count(scoreable non-native substitutions)');
    assert.equal(maximumDelta.values[0]?.value, expectedDelta);
    assert.equal(maximumDelta.descriptor.formula, 'max(scoreable non-native score) - native score');
    assert.equal(maximumDelta.descriptor.direction, 'neutral');
});

test('native FrustraMPNN workbench legend renders categorical threshold classes', () => {
    const bundle = createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [residue()], structureMap: structureMap(),
    });
    const html = renderToStaticMarkup(React.createElement(MetricLegendPanel, { layer: bundle.layers[0] }));
    assert.match(html, /Highly frustrated/);
    assert.match(html, /Neutral/);
    assert.match(html, /Minimally frustrated/);
    assert.match(html, /≤ -1\.0/);
    assert.match(html, /≥ 0\.58/);
});

test('FrustraMPNN layers pass the real metric registry and Molstar projection seam', () => {
    const bundle = createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [residue()], structureMap: structureMap(),
    });
    const registry = new MetricRegistry();
    for (const layer of bundle.layers) assert.equal(registry.register(layer).status, 'ok');
    const projected = projectResidueMetricLayer(bundle.layers[0]);
    assert.equal(projected.status, 'ok');
    if (projected.status === 'ok') {
        assert.equal(projected.value.points[0]?.residue.labelAsymId, 'A');
        assert.equal(projected.value.points[0]?.residue.authSeqId, 42);
    }
});

test('missing native evidence remains unavailable and is never imputed', () => {
    const missingNative = residue();
    missingNative.slots = missingNative.slots.map((slot) => slot.mutation_aa === missingNative.wt
        ? { ...slot, score: null, class: null, scoreable: false, status: 'nonfinite_score', reason: 'native score absent' }
        : slot);
    const bundle = createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [missingNative], structureMap: structureMap(),
    });

    assert.equal(bundle.layers[0].values[0]?.value, null);
    assert.equal(bundle.layers[0].values[0]?.missingness, 'unavailable');
    assert.equal(bundle.layers[2].values[0]?.value, null);
    assert.equal(bundle.layers[2].values[0]?.missingness, 'unavailable');
});

test('structure-map identity disagreement fails closed instead of guessing a residue number', () => {
    assert.throws(() => createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [residue()],
        structureMap: structureMap({ auth_seq_id: 43 }),
    }), /identity mismatch/i);
});

test('viewer residue selection resolves the exact canonical 20-slot profile', () => {
    const expected = residue();
    const bundle = createFrustraMpnnViewerMetrics({
        requestId: 'request-1', candidateId: 'candidate-1', residues: [expected], structureMap: structureMap(),
    });
    const selected = resolveFrustraMpnnResidueProfile(bundle, {
        documentId: 'primary', labelAsymId: 'A', labelSeqId: 7,
        authAsymId: 'AUTH', authSeqId: 42, insertionCode: 'A',
    });
    assert.equal(selected?.key, expected.key);
    assert.equal(selected?.slots.length, 20);
    assert.equal(resolveFrustraMpnnResidueProfile(bundle, {
        documentId: 'primary', labelAsymId: 'B', labelSeqId: 7,
    }), undefined);
});

test('complete landscape collection follows bounded monotonic pages and validates one candidate', async () => {
    const rows = residue().slots;
    const pages: CmLandscapePage[] = [
        { request_id: 'request-1', candidate_id: 'candidate-1', entity_instance_id: null, sequence_start: null, sequence_end: null, offset: 0, limit: 10, next_offset: 10, rows: rows.slice(0, 10) },
        { request_id: 'request-1', candidate_id: 'candidate-1', entity_instance_id: null, sequence_start: null, sequence_end: null, offset: 10, limit: 10, next_offset: null, rows: rows.slice(10) },
    ];
    const complete = await collectCompleteFrustraMpnnLandscape(async (offset) => pages.find((page) => page.offset === offset)!, 100);
    assert.equal(complete.length, 20);

    await assert.rejects(() => collectCompleteFrustraMpnnLandscape(async () => ({ ...pages[0], next_offset: 0 }), 100), /monotonic/i);
    await assert.rejects(() => collectCompleteFrustraMpnnLandscape(async () => ({ ...pages[0], next_offset: null, rows: [...rows, ...rows] }), 20), /bounded/i);
});

test('canonical conformational viewer uses the shared metric workbench, not a global substitution dropdown', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components/conformationalMapping/ConformationalMappingViewer.tsx'), 'utf8');
    assert.doesNotMatch(source, /landscapeMutation|Landscape substitution/);
    assert.match(source, /collectCompleteFrustraMpnnLandscape/);
    assert.match(source, /metricLayers=\{frustraMpnnMetrics\.layers\}/);
    assert.match(source, /onMetricSelection=\{setFrustraMpnnSelection\}/);
    assert.match(source, /Exact-20 residue profile/);
});

test('generic structure viewer cannot reactivate the retired position-guessed frustration layer', () => {
    const pane = readFileSync(resolve(process.cwd(), 'src/components/StructureViewerPane.tsx'), 'utf8');
    const results = readFileSync(resolve(process.cwd(), 'src/components/ResultsViewer.tsx'), 'utf8');
    assert.doesNotMatch(pane, /frustrationResidueColors|frustrationColor|residueNumbers\[residue\.pos\]|frustration-index/);
    assert.doesNotMatch(results, /setColorMode\([^)]*frustration|frustration_residues\?\.length \? 'frustration'/);
});
