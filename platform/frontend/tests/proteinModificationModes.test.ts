import assert from 'node:assert/strict';
import test from 'node:test';

import { DE_NOVO_MODIFICATION_MODE_CARDS } from '../src/components/proteinModificationModes';

test('De Novo Design owns one RFD3 iteration workbench', () => {
    assert.deepEqual(
        DE_NOVO_MODIFICATION_MODE_CARDS.map((card) => card.id),
        ['de_novo_design', 'rfd3_iteration', 'shape_blueprint'],
    );

    const workbench = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'rfd3_iteration');
    assert.equal(workbench?.label, 'RFD3 Iteration Workbench');
    assert.match(workbench?.description ?? '', /Mol\*/i);
    assert.match(workbench?.description ?? '', /native RFD3 output or downstream sequence design and validation/i);
});
