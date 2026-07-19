import assert from 'node:assert/strict';
import test from 'node:test';

import { adaptLegacyResidueColors } from '../src/structureViewer/adapters/residueColorSelections.js';

test('adapts canonical and legacy residue-color keys into label namespace selections', () => {
    const colors = new Map([
        ['H:42', { r: 1, g: 2, b: 3 }],
        ['A7', { r: 4, g: 5, b: 6 }],
        ['Heavy:101', { r: 7, g: 8, b: 9 }],
    ]);

    assert.deepEqual(adaptLegacyResidueColors(colors), {
        selections: [
            { struct_asym_id: 'A', residue_number: 7, color: { r: 4, g: 5, b: 6 } },
            { struct_asym_id: 'H', residue_number: 42, color: { r: 1, g: 2, b: 3 } },
            { struct_asym_id: 'Heavy', residue_number: 101, color: { r: 7, g: 8, b: 9 } },
        ],
        rejected: [],
    });
});

test('fails closed for malformed, non-integer, or insertion-code-like keys', () => {
    const colors = new Map([
        ['', { r: 1, g: 1, b: 1 }],
        [':42', { r: 2, g: 2, b: 2 }],
        ['A:', { r: 3, g: 3, b: 3 }],
        ['A:42B', { r: 4, g: 4, b: 4 }],
        ['A:1.5', { r: 5, g: 5, b: 5 }],
    ]);

    const result = adaptLegacyResidueColors(colors);
    assert.deepEqual(result.selections, []);
    assert.deepEqual(result.rejected.map((entry) => entry.key), ['', ':42', 'A:', 'A:1.5', 'A:42B']);
});
