import assert from 'node:assert/strict';
import test from 'node:test';

import {
    adaptResidueMetricLayer,
    buildResidueMetricLayer,
    canonicalResidueKey,
    type MolstarResidueMetricLayer,
} from '../src/lib/molstar-metrics.js';

const descriptor = {
    id: 'plddt',
    label: 'pLDDT',
    semanticType: 'confidence' as const,
    units: null,
    direction: 'higher_is_better' as const,
    source: 'Boltz-2 confidence JSON',
    provenance: { model: 'boltz2' },
};

const color = { r: 0, g: 83, b: 214 };

test('canonical residue keys retain label, author, and insertion-code namespaces', () => {
    const first = canonicalResidueKey({
        labelAsymId: 'A',
        authAsymId: 'H',
        labelSeqId: 45,
        authSeqId: 52,
        insertionCode: 'A',
    });
    const second = canonicalResidueKey({
        labelAsymId: 'A',
        authAsymId: 'H',
        labelSeqId: 46,
        authSeqId: 52,
        insertionCode: 'B',
    });

    assert.notEqual(first, second);
    assert.match(first, /label_asym=A/);
    assert.match(first, /auth_seq=52/);
    assert.match(first, /ins=A/);
});

test('adapter emits the BMS direct Mol* query contract and metric tooltips', () => {
    const layer: MolstarResidueMetricLayer = {
        scope: 'residue-scalar',
        descriptor,
        points: [{
            residue: {
                entityId: '1',
                labelAsymId: 'A',
                authAsymId: 'H',
                labelSeqId: 45,
                authSeqId: 52,
                insertionCode: 'A',
            },
            value: 93.4,
            color,
        }],
    };

    const result = adaptResidueMetricLayer(layer);

    assert.deepEqual(result.rejected, []);
    assert.deepEqual(result.colorSelections, [{
        entity_id: '1',
        struct_asym_id: 'A',
        auth_asym_id: 'H',
        residue_number: 45,
        auth_residue_number: 52,
        auth_ins_code_id: 'A',
        color,
    }]);
    assert.deepEqual(result.tooltipSelections, [{
        entity_id: '1',
        struct_asym_id: 'A',
        auth_asym_id: 'H',
        residue_number: 45,
        auth_residue_number: 52,
        auth_ins_code_id: 'A',
        tooltip: 'pLDDT: 93.4 · Boltz-2 confidence JSON',
    }]);
});

test('adapter fails closed for unsupported operator/instance identity', () => {
    const result = adaptResidueMetricLayer({
        scope: 'residue-scalar',
        descriptor,
        points: [{
            residue: { labelAsymId: 'A', labelSeqId: 45, instanceId: '1_555' },
            value: 93.4,
            color,
        }],
    });

    assert.equal(result.colorSelections.length, 0);
    assert.equal(result.tooltipSelections.length, 0);
    assert.match(result.rejected[0].reason, /operator\/instance identity is not supported/);
});

test('adapter rejects unnumbered points and duplicate canonical identities', () => {
    const point = { residue: { labelAsymId: 'A', labelSeqId: 7 }, value: 81, color };
    const result = adaptResidueMetricLayer({
        scope: 'residue-scalar',
        descriptor,
        points: [
            { residue: { labelAsymId: 'A' }, value: 80, color },
            point,
            point,
        ],
    });

    assert.equal(result.colorSelections.length, 1);
    assert.equal(result.rejected.length, 2);
    assert.match(result.rejected[0].reason, /supported residue number/);
    assert.match(result.rejected[1].reason, /duplicate canonical residue identity/);
});

test('chain-series builder never invents residue numbering', () => {
    const layer = buildResidueMetricLayer({
        descriptor,
        chains: {
            A: { labelAsymId: 'A', labelSeqIds: [10, 11], values: [88, 89] },
            B: { authAsymId: 'H', authSeqIds: [100], insertionCodes: ['A'], values: [77] },
            C: { values: [66] },
        },
        colorForValue: () => color,
    });
    const result = adaptResidueMetricLayer(layer);

    assert.deepEqual(result.colorSelections.map((item) => [
        item.struct_asym_id,
        item.auth_asym_id,
        item.residue_number,
        item.auth_residue_number,
        item.auth_ins_code_id,
    ]), [
        ['A', undefined, 10, undefined, undefined],
        ['A', undefined, 11, undefined, undefined],
        [undefined, 'H', undefined, 100, 'A'],
    ]);
    assert.equal(result.rejected.length, 1);
    assert.equal(result.rejected[0].point.value, 66);
});

test('pairwise matrices are not representable as residue scalar layers', () => {
    const source = JSON.stringify({ scope: 'chain-pair-matrix', values: [[0.9, 0.2]] });
    assert.doesNotMatch(source, /"scope":"residue-scalar"/);
});
