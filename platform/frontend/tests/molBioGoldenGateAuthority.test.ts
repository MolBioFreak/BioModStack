import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    buildAssemblyReloadOperationParams,
    buildGoldenGateAssemblyRequest,
    requireGoldenGateAssemblyResponse,
} from '../src/lib/goldenGateAuthority';

const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const panel = readFileSync(
    new URL('../src/components/MolBioToolkit/panels/AssemblyPanel.tsx', import.meta.url),
    'utf8',
);
const fragments = [{ id: 'f1', name: 'one', sequence: 'ACGT' }];
const options = {
    catalog: { catalog_id: 'catalog-v1', catalog_sha256: 'a'.repeat(64) },
    enzymes: [{ enzyme_id: 'BsmBI', canonical_name: 'BsmBI', overhang_length: 4 }],
};

test('Golden Gate request contract requires stable catalog authority', () => {
    assert.match(api, /GoldenGateAssemblyRequest[\s\S]{0,180}enzyme_id: string/);
    assert.match(api, /GoldenGateAssemblyRequest[\s\S]{0,260}catalog_id: string/);
    assert.match(api, /GoldenGateAssemblyRequest[\s\S]{0,360}expected_catalog_sha256: string/);
    assert.doesNotMatch(api, /GoldenGateAssemblyRequest[\s\S]{0,180}enzyme_id\?: string/);
    assert.doesNotMatch(api, /GoldenGateAssemblyOptionsResponse[\s\S]{0,300}site: string/);
    assert.doesNotMatch(panel, /enzyme_name: goldenGateEnzyme/);
    assert.match(panel, /enzyme\.canonical_name/);
    assert.doesNotMatch(panel, /enzyme\.site/);
});

test('production request boundary submits exact loaded authority and stable ID', () => {
    const request = buildGoldenGateAssemblyRequest({
        fragments,
        circular: true,
        selectedEnzymeId: 'BsmBI',
        options,
    });
    assert.deepEqual(request, {
        fragments,
        circular: true,
        enzyme_id: 'BsmBI',
        catalog_id: 'catalog-v1',
        expected_catalog_sha256: 'a'.repeat(64),
        new_name: undefined,
        save_description: undefined,
    });
    assert.equal('recognition_site' in request, false);
    assert.equal('site' in request, false);
    assert.equal('cut_index' in request, false);
});

test('strict response and unsaved reload preserve exact Golden Gate authority', () => {
    const authority = {
        enzyme_id: 'BsmBI',
        catalog_id: 'catalog-v1',
        catalog_sha256: 'a'.repeat(64),
    };
    const response = requireGoldenGateAssemblyResponse({
        product: {
            sequence: 'ACGT', circular: false, length: 4, mode: 'golden_gate', fragments: [], junctions: [],
            warnings: [], validation_notes: [], golden_gate_authority: authority,
        },
        message: 'validated',
    });

    assert.deepEqual(response.product.golden_gate_authority, authority);
    assert.deepEqual(buildAssemblyReloadOperationParams(response.product), {
        mode: 'golden_gate', fragment_count: 0, warnings: [], validation_notes: [], ...authority,
    });
    assert.throws(
        () => requireGoldenGateAssemblyResponse({
            product: { ...response.product, golden_gate_authority: undefined }, message: 'invalid',
        }),
        /Golden Gate catalog authority/,
    );
});

test('missing or stale backend options cannot silently submit BsaI', () => {
    for (const [selectedEnzymeId, authority] of [
        ['', options],
        ['BsaI', options],
        ['BsaI', null],
    ] as const) {
        assert.throws(
            () => buildGoldenGateAssemblyRequest({
                fragments,
                circular: false,
                selectedEnzymeId,
                options: authority,
            }),
            /currently loaded Golden Gate compatible catalog enzyme/,
        );
    }
});
