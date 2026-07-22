import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    applyImportedTopology,
    findLinearizationBlockers,
    setSequenceTopology,
} from '../src/components/MolBioToolkit/utils/topology.js';

test('import topology override preserves, circularizes, or linearizes explicitly', () => {
    assert.equal(applyImportedTopology(true, 'preserve'), true);
    assert.equal(applyImportedTopology(false, 'preserve'), false);
    assert.equal(applyImportedTopology(false, 'circular'), true);
    assert.equal(applyImportedTopology(true, 'linear'), false);
});

test('circularizing preserves sequence and geometry while changing only topology', () => {
    const linear = {
        sequence: 'AACCGGTT',
        circular: false,
        features: [{ id: 'f1', start: 1, end: 4, segments: [{ start: 1, end: 4 }] }],
        primers: [{ id: 'p1', start: 0, end: 3, sites: [{ start: 0, end: 3 }] }],
    };

    assert.deepEqual(setSequenceTopology(linear, true), { ...linear, circular: true });
});

test('linearization fails closed when features or primers traverse the current origin', () => {
    const blockers = findLinearizationBlockers({
        sequence: 'AACCGGTT',
        circular: true,
        features: [{ id: 'wrap-feature', start: 0, end: 8, segments: [{ start: 6, end: 8 }, { start: 0, end: 2 }] }],
        primers: [{ id: 'wrap-primer', start: 6, end: 2, sites: [{ start: 6, end: 8 }, { start: 0, end: 2 }] }],
    });

    assert.deepEqual(blockers, ['1 origin-spanning feature', '1 origin-spanning primer']);
    assert.throws(
        () => setSequenceTopology({ sequence: 'AACCGGTT', circular: true, features: [], primers: [{ start: 6, end: 2 }] }, false),
        /rotate the origin/i,
    );
});

test('input and edit surfaces expose explicit topology controls and history labels', () => {
    const modal = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/MolecularInputModal.tsx'), 'utf8');
    const editPanel = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/panels/EditPanel.tsx'), 'utf8');
    const toolkit = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/MolBioToolkitV2.tsx'), 'utf8');

    assert.match(modal, /importTopology/);
    assert.match(modal, /Preserve file topology/);
    assert.match(modal, /Force circular/);
    assert.match(editPanel, /Circularize construct/);
    assert.match(editPanel, /Linearize at current origin/);
    assert.match(toolkit, /actionLabel/);
});