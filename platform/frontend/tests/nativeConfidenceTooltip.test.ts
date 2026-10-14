import assert from 'node:assert/strict';
import test from 'node:test';
import { projectAtomMetricLayer, projectResidueMetricLayer } from '../src/structureViewer/metrics/metricProjection.js';
import type { MetricLayer } from '../src/structureViewer/metrics/metricContracts.js';

const identity = { documentId: 'selected-native-sample', labelAsymId: 'B', labelSeqId: 93,
    authAsymId: 'B', authSeqId: 93, componentId: 'LEU', labelAtomId: 'CD1' };
const descriptor = { id: 'native-plddt', label: 'Native atom pLDDT', dimension: 'atom-scalar' as const,
    units: 'fraction', direction: 'higher_is_better' as const, valueRange: [0, 1] as const,
    projectionPolicy: 'direct' as const, normalization: 'none' as const,
    provenance: { source: 'Verified native confidence vector', artifactSha256: 'retained-source-identity' } };

test('native atom hover shows exact atom identity and display precision without altering evidence', () => {
    const value = 0.9736922383308411;
    const layer: MetricLayer = { descriptor, values: [{ identity, value, displayColor: '#22d3ee' }] };
    const before = JSON.stringify(layer);
    const [point] = projectAtomMetricLayer(layer);
    assert.equal(point!.tooltip, 'B · LEU93 · CD1 — Native atom pLDDT: 97.4 /100');
    assert.equal(point!.identity, identity);
    assert.deepEqual(point!.color, { r: 34, g: 211, b: 238 });
    assert.equal(JSON.stringify(layer), before);
    assert.ok(!point!.tooltip.includes('Verified native'));
    assert.ok(!point!.tooltip.includes(String(value)));
});

test('author insertion and alternate atom identity remain explicit', () => {
    const atom = { ...identity, authAsymId: 'C', authSeqId: 120, insertionCode: 'A', altLoc: 'B' };
    const [point] = projectAtomMetricLayer({ descriptor, values: [{ identity: atom, value: 0.81 }] });
    assert.equal(point!.tooltip, 'C · LEU120A · CD1 · alt B — Native atom pLDDT: 81.0 /100');
    assert.equal(point!.identity, atom);
});

test('unrelated fraction metrics are not silently converted into confidence percentages', () => {
    const [point] = projectAtomMetricLayer({ descriptor: { ...descriptor, id: 'other', label: 'Other metric' },
        values: [{ identity, value: 0.000003246345 }] });
    assert.equal(point!.tooltip, 'B · LEU93 · CD1 — Other metric: 0.000003246 fraction');
});

test('residue hover precision is presentation only and retains complete provenance', () => {
    const residue = { documentId: 'native', labelAsymId: 'A', labelSeqId: 17, componentId: 'ALA' };
    const value = 0.9178343544;
    const layer: MetricLayer = { descriptor: { ...descriptor, label: 'Native residue pLDDT', dimension: 'residue-scalar' },
        values: [{ identity: residue, value }] };
    const result = projectResidueMetricLayer(layer);
    assert.equal(result.status, 'ok');
    if (result.status !== 'ok') return;
    assert.equal(result.value.points[0]!.tooltip, 'A · ALA17 — Native residue pLDDT: 91.8 /100');
    assert.equal(result.value.points[0]!.value, value);
    assert.equal(result.value.descriptor.source, descriptor.provenance.source);
    assert.equal(layer.values[0]!.value, value);
});
