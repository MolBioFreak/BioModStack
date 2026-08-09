import assert from 'node:assert/strict';
import test from 'node:test';

import { DE_NOVO_MODIFICATION_MODE_CARDS } from '../src/components/proteinModificationModes';

test('De Novo Design owns the native RFD3 local-redesign child mode', () => {
    assert.deepEqual(
        DE_NOVO_MODIFICATION_MODE_CARDS.map((card) => card.id),
        ['de_novo_design', 'rfd3_local_redesign', 'shape_blueprint'],
    );

    const localRedesign = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'rfd3_local_redesign');
    assert.equal(localRedesign?.label, 'RFD3 Local Redesign');
    assert.match(localRedesign?.description ?? '', /native RFD3/i);
});
