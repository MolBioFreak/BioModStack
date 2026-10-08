import { strict as assert } from 'node:assert';
import test from 'node:test';

import {
    FEATURE_TYPES,
    getFeatureColor,
    normalizeFeatureType,
} from '../src/components/MolBioToolkit/featureCatalog.js';
import {
    featureCoordinateLabel,
    featureHighlightRegions,
    type FeatureRecord,
} from '../src/components/MolBioToolkit/utils/features.js';

const compoundFeature: FeatureRecord = {
    id: 'feature-wrap',
    name: 'Origin wrap',
    type: 'misc_feature',
    start: 0,
    end: 16,
    strand: 1,
    color: '#06b6d4',
    segments: [
        { start: 12, end: 16 },
        { start: 0, end: 4 },
    ],
};

test('compound feature highlight uses every authoritative segment rather than aggregate bounds', () => {
    assert.deepEqual(featureHighlightRegions(compoundFeature, '#06b6d4'), [
        { start: 12, end: 16, color: '#06b6d4', label: 'Origin wrap' },
        { start: 0, end: 4, color: '#06b6d4', label: 'Origin wrap' },
    ]);
});

test('compound feature coordinate summary names each segment', () => {
    assert.equal(featureCoordinateLabel(compoundFeature), '13–16 + 1–4');
});

test('UTR aliases normalize to canonical INSDC feature keys and colors', () => {
    assert.equal(normalizeFeatureType('5UTR'), "5'UTR");
    assert.equal(normalizeFeatureType("5'UTR"), "5'UTR");
    assert.equal(normalizeFeatureType('5′ UTR'), "5'UTR");
    assert.equal(normalizeFeatureType('3UTR'), "3'UTR");
    assert.equal(normalizeFeatureType("3' UTR"), "3'UTR");
    assert.equal(getFeatureColor('5UTR'), getFeatureColor("5'UTR"));
    assert.equal(getFeatureColor('3UTR'), getFeatureColor("3'UTR"));

    const catalogValues = FEATURE_TYPES.map((entry) => entry.value);
    assert.ok(catalogValues.includes("5'UTR"));
    assert.ok(catalogValues.includes("3'UTR"));
    assert.ok(!catalogValues.includes('5UTR'));
    assert.ok(!catalogValues.includes('3UTR'));
});
