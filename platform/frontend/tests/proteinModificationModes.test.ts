import assert from 'node:assert/strict';
import test from 'node:test';

import { DE_NOVO_MODIFICATION_MODE_CARDS } from '../src/components/proteinModificationModes';

test('De Novo Design owns the native RFD3 local-redesign and region-redesign child modes', () => {
    assert.deepEqual(
        DE_NOVO_MODIFICATION_MODE_CARDS.map((card) => card.id),
        ['de_novo_design', 'rfd3_local_redesign', 'region_redesign', 'shape_blueprint'],
    );

    const localRedesign = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'rfd3_local_redesign');
    assert.equal(localRedesign?.label, 'RFD3 Local Redesign');
    assert.match(localRedesign?.description ?? '', /native RFD3/i);

    const regionRedesign = DE_NOVO_MODIFICATION_MODE_CARDS.find((card) => card.id === 'region_redesign');
    assert.equal(regionRedesign?.label, 'Region Redesign');
    assert.match(regionRedesign?.description ?? '', /FA-MPNN/i);
    assert.match(regionRedesign?.description ?? '', /Protenix V2/i);
});
