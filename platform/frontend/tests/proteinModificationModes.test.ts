import assert from 'node:assert/strict';
import test from 'node:test';

import { DE_NOVO_MODIFICATION_MODE_CARDS } from '../src/components/proteinModificationModes';

test('De Novo Design prefers native RFD3 while preserving iteration and shape blueprint', () => {
    assert.deepEqual(
        DE_NOVO_MODIFICATION_MODE_CARDS.map((card) => card.id),
        ['de_novo_design', 'rfd3_iteration', 'shape_blueprint'],
    );

    const deNovo = DE_NOVO_MODIFICATION_MODE_CARDS[0];
    assert.equal(deNovo?.id, 'de_novo_design');
    assert.equal(deNovo?.label, 'Generate');
    assert.match(deNovo?.description ?? '', /native controls/i);

    const workbench = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'rfd3_iteration');
    assert.equal(workbench?.label, 'Redesign structure');
    assert.match(workbench?.description ?? '', /Mol\*/i);
    assert.match(workbench?.description ?? '', /native RFD3 output or downstream sequence design and validation/i);
});
