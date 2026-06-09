import assert from 'node:assert/strict';
import test from 'node:test';

import {
    getUnsupportedResultReason,
    isUnsupportedResult,
    supportsAnalyzer,
    supportsViewerCapability,
} from '../src/lib/resultCapabilities.js';

test('unknown metric-shaped design remains unsupported without explicit contract', () => {
    const design = {
        analysis_contract_id: null,
        supported_analyzers: [],
        viewer_capabilities: [],
        plddt_overall: 99.1,
        fampnn_psce: 0.12,
        ppiflow_objective_score: -42,
    };

    assert.equal(isUnsupportedResult(design), true);
    assert.equal(supportsAnalyzer(design, 'sequence_design_v1'), false);
    assert.equal(supportsViewerCapability(design, 'sequence_design_metrics'), false);
    assert.match(getUnsupportedResultReason(design), /unsupported/i);
});

test('declared PPIFlow contract enables only PPIFlow capabilities', () => {
    const design = {
        analysis_contract_id: 'ppiflow_maturation_v1',
        supported_analyzers: ['ppiflow_maturation_v1'],
        viewer_capabilities: ['result_filter', 'structure_viewer', 'ppiflow_maturation_metrics'],
    };

    assert.equal(isUnsupportedResult(design), false);
    assert.equal(supportsAnalyzer(design, 'ppiflow_maturation_v1'), true);
    assert.equal(supportsViewerCapability(design, 'ppiflow_maturation_metrics'), true);
    assert.equal(supportsAnalyzer(design, 'sequence_design_v1'), false);
    assert.equal(supportsViewerCapability(design, 'sequence_design_metrics'), false);
});

test('legacy rows without capability array can fall back to supported analyzer ids', () => {
    const design = {
        analysis_contract_id: 'sequence_design_v1',
        supported_analyzers: ['sequence_design_v1'],
    };

    assert.equal(supportsViewerCapability(design, 'sequence_design_metrics'), true);
    assert.equal(supportsViewerCapability(design, 'ppiflow_maturation_metrics'), false);
});
