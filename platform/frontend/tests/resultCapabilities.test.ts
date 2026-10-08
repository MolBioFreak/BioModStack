import assert from 'node:assert/strict';
import test from 'node:test';

import * as resultCapabilities from '../src/lib/resultCapabilities.js';

const {
    getUnsupportedResultReason,
    isUnsupportedResult,
    supportsAnalyzer,
    supportsViewerCapability,
} = resultCapabilities;

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
        supported_analyzers: ['structure_summary'],
    };

    assert.equal(supportsViewerCapability(design, 'sequence_design_metrics'), false);
    assert.equal(supportsViewerCapability(design, 'ppiflow_maturation_metrics'), false);
});

test('visible review tabs are derived from declared capabilities', () => {
    const getVisibleReviewTabs = (resultCapabilities as Record<string, unknown>).getVisibleReviewTabs;
    assert.equal(typeof getVisibleReviewTabs, 'function');

    const structure = {
        analysis_contract_id: 'structure_prediction_v1',
        supported_analyzers: ['structure_summary'],
        viewer_capabilities: ['structure_viewer', 'structure_confidence_metrics'],
        review_artifact_manifest: {
            schema: 'bms.review-artifacts.v1',
            artifacts: { structure: { state: 'ready' as const } },
        },
    };
    const antibody = {
        analysis_contract_id: 'antibody_backbone_v1',
        supported_analyzers: ['structure_summary', 'antibody_annotation_pack'],
        viewer_capabilities: ['result_filter', 'structure_viewer', 'antibody_backbone_metrics'],
        review_artifact_manifest: {
            schema: 'bms.review-artifacts.v1',
            artifacts: { structure: { state: 'ready' as const } },
        },
    };

    assert.deepEqual((getVisibleReviewTabs as (design: unknown) => string[])({
        analysis_contract_id: null,
        supported_analyzers: [],
        viewer_capabilities: [],
    }), ['overview', 'table']);
    assert.deepEqual((getVisibleReviewTabs as (design: unknown) => string[])(structure), [
        'overview',
        'charts',
        'structure',
        'table',
        'compare_designs',
        'compare',
    ]);
    assert.deepEqual((getVisibleReviewTabs as (design: unknown) => string[])(antibody), [
        'overview',
        'charts',
        'structure',
        'antibody',
        'table',
        'compare_designs',
        'compare',
    ]);
});

test('analyzer availability requires both profile authorization and ready artifacts', () => {
    const design = {
        analysis_contract_id: 'structure_prediction_v1',
        supported_analyzers: ['structure_summary', 'pae_matrix'],
        viewer_capabilities: ['structure_viewer', 'structure_confidence_metrics'],
        review_artifact_manifest: {
            schema: 'bms.review-artifacts.v1',
            artifacts: {
                structure: { state: 'ready' as const },
                aligned_error: { state: 'missing' as const },
            },
        },
    };

    assert.equal(resultCapabilities.isAnalyzerAvailable(design, 'structure_summary'), true);
    assert.equal(resultCapabilities.isAnalyzerAvailable(design, 'pae_matrix'), false);
    assert.equal(resultCapabilities.isAnalyzerAvailable(design, 'ipsae_interface'), false);
    assert.equal(resultCapabilities.isAnalyzerAvailable({
        analysis_contract_id: 'structure_prediction_v1',
        supported_analyzers: ['structure_summary'],
        viewer_capabilities: ['structure_viewer'],
    }, 'structure_summary'), false);
});

test('binder and antibody table columns require antibody capability', () => {
    const getReviewColumnCapabilities = (resultCapabilities as Record<string, unknown>).getReviewColumnCapabilities;
    assert.equal(typeof getReviewColumnCapabilities, 'function');

    const generic = {
        analysis_contract_id: 'structure_prediction_v1',
        viewer_capabilities: ['structure_viewer', 'structure_confidence_metrics'],
    };
    const antibody = {
        analysis_contract_id: 'antibody_backbone_v1',
        viewer_capabilities: ['structure_viewer', 'antibody_backbone_metrics'],
    };

    assert.deepEqual((getReviewColumnCapabilities as (design: unknown) => unknown)(generic), {
        antibody: false,
        interface: false,
        sequenceDesign: false,
    });
    assert.deepEqual((getReviewColumnCapabilities as (design: unknown) => unknown)(antibody), {
        antibody: true,
        interface: true,
        sequenceDesign: false,
    });
});
