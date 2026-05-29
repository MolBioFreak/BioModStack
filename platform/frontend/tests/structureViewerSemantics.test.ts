import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    buildConforNetsConformerNavigation,
    buildConforNetsConformerSet,
    resolveConforNetsOverlayIds,
    buildStructureViewerQuickViews,
    buildStructureViewerSections,
    buildPlddtResidueColorMap,
    buildStructureViewerSummaryCards,
    getConforNetsDefaultChainId,
    getConforNetsScalarPlddt,
    getConforNetsSampleIndex,
    isConforNetsDesign,
    resolveStructureViewerConfidenceSemantics,
} from '../src/components/structureViewerSemantics.js';

test('oligo jobs use design-confidence semantics instead of pLDDT wording', () => {
    const semantics = resolveStructureViewerConfidenceSemantics({
        activeJobModelId: 'oligo_builder',
        designLens: 'validation',
    });

    assert.equal(semantics.shortLabel, 'Design Conf.');
    assert.equal(semantics.headlineLabel, 'Design Confidence');
    assert.equal(semantics.profileTitle, 'Design Confidence Profile');
    assert.equal(semantics.legendHeading, 'Design-confidence bands');
    assert.equal(semantics.preferChainColoring, true);
});

test('validation and protenix outputs keep pLDDT semantics in the viewer', () => {
    assert.equal(
        resolveStructureViewerConfidenceSemantics({
            activeJobModelId: 'boltz2',
            designLens: 'validation',
        }).headlineLabel,
        'pLDDT',
    );

    assert.equal(
        resolveStructureViewerConfidenceSemantics({
            activeJobModelId: 'protenix',
            designLens: 'protenix',
        }).profileTitle,
        'pLDDT Profile',
    );

    assert.equal(
        resolveStructureViewerConfidenceSemantics({
            activeJobModelId: 'rfantibody',
            designLens: 'rfantibody',
        }).headlineLabel,
        'RF pLDDT',
    );
});

test('viewer sections only appear when their underlying analyses or metrics exist', () => {
    const minimal = buildStructureViewerSections({
        hasResidueConfidence: true,
        hasPaeMatrix: false,
        hasStructureSummary: false,
        hasIpsaeInterface: false,
        hasChainPairIptm: false,
        hasContactMap: false,
        hasFampnnPsceProfile: false,
        hasFrustrationSummary: false,
    });
    assert.deepEqual(minimal.map((section) => section.id), ['summary', 'confidence']);

    const full = buildStructureViewerSections({
        hasResidueConfidence: true,
        hasPaeMatrix: true,
        hasStructureSummary: true,
        hasIpsaeInterface: true,
        hasChainPairIptm: true,
        hasContactMap: true,
        hasFampnnPsceProfile: true,
        hasFrustrationSummary: true,
    });
    assert.deepEqual(full.map((section) => section.id), ['summary', 'confidence', 'interface', 'geometry', 'designability']);
});

test('quick views map semantic sections onto concrete overlay and color modes', () => {
    const quickViews = buildStructureViewerQuickViews({
        confidenceLabel: 'pLDDT',
        hasResidueConfidence: true,
        hasPaeMatrix: true,
        hasStructureSummary: true,
        hasIpsaeInterface: true,
        hasChainPairIptm: true,
        hasContactMap: true,
        hasFampnnPsceProfile: true,
        hasFrustrationSummary: true,
        hasCdrOverlay: true,
    });

    assert.deepEqual(quickViews.map((view) => view.id), ['summary', 'confidence', 'interface', 'geometry', 'designability', 'cdr']);
    assert.deepEqual(quickViews.map((view) => [view.id, view.overlayView, view.colorMode]), [
        ['summary', 'metrics', 'default'],
        ['confidence', 'plddt', 'plddt'],
        ['interface', 'metrics', 'default'],
        ['geometry', 'pae', 'default'],
        ['designability', 'psce', 'fampnn_psce'],
        ['cdr', 'metrics', 'cdr'],
    ]);
});

test('designability quick view falls back to frustration coloring when no PSCE profile exists', () => {
    const quickViews = buildStructureViewerQuickViews({
        confidenceLabel: 'Design Confidence',
        hasResidueConfidence: false,
        hasPaeMatrix: false,
        hasStructureSummary: false,
        hasIpsaeInterface: false,
        hasChainPairIptm: false,
        hasContactMap: false,
        hasFampnnPsceProfile: false,
        hasFrustrationSummary: true,
        hasCdrOverlay: false,
    });

    assert.deepEqual(quickViews, [
        {
            id: 'summary',
            label: 'Summary',
            sectionId: 'summary',
            overlayView: 'metrics',
            colorMode: 'default',
        },
        {
            id: 'designability',
            label: 'Designability',
            sectionId: 'designability',
            overlayView: 'metrics',
            colorMode: 'frustration',
        },
    ]);
});

test('fampnn designs keep the designability section and quick view visible before PSCE finishes computing', () => {
    const sections = buildStructureViewerSections({
        hasResidueConfidence: false,
        hasPaeMatrix: false,
        hasStructureSummary: false,
        hasIpsaeInterface: false,
        hasChainPairIptm: false,
        hasContactMap: false,
        hasFampnnDesign: true,
        hasFampnnPsceProfile: false,
        hasFrustrationSummary: false,
    });
    assert.deepEqual(sections.map((section) => section.id), ['summary', 'designability']);

    const quickViews = buildStructureViewerQuickViews({
        confidenceLabel: 'Design Confidence',
        hasResidueConfidence: false,
        hasPaeMatrix: false,
        hasStructureSummary: false,
        hasIpsaeInterface: false,
        hasChainPairIptm: false,
        hasContactMap: false,
        hasFampnnDesign: true,
        hasFampnnPsceProfile: false,
        hasFrustrationSummary: false,
        hasCdrOverlay: false,
    });
    assert.deepEqual(quickViews, [
        {
            id: 'summary',
            label: 'Summary',
            sectionId: 'summary',
            overlayView: 'metrics',
            colorMode: 'default',
        },
        {
            id: 'designability',
            label: 'Designability',
            sectionId: 'designability',
            overlayView: 'metrics',
            colorMode: 'default',
        },
    ]);
});

test('summary cards surface hidden binder, target, interface, and complex metrics when present', () => {
    const cards = buildStructureViewerSummaryCards({
        confidenceLabel: 'pLDDT',
        designLens: 'validation',
        selectedDesign: {
            plddt_overall: 91.2,
            plddt_binder: 88.4,
            plddt_target: 93.1,
            pae_overall: 6.5,
            pae_interaction: 9.75,
            ptm: 0.71,
            iptm: 0.63,
            ipsae: 0.55,
            complex_iplddt: 0.67,
            complex_ipde: 12.4,
        },
    });

    assert.deepEqual(cards.map((card) => card.label), [
        'pLDDT',
        'Binder pLDDT',
        'Target pLDDT',
        'PAE',
        'Interaction PAE',
        'pTM',
        'iPTM',
        'ipSAE',
        'Complex iPLDDT',
        'Interface PDE',
    ]);
});

test('ConforNets confidence cards surface scalar confidence, error, and landscape metrics', () => {
    const cards = buildStructureViewerSummaryCards({
        confidenceLabel: 'pLDDT',
        designLens: 'validation',
        selectedDesign: {
            artifact_group: 'confornets',
            plddt_overall: 76.3,
            confidence_metrics: {
                confornets_sample: {
                    frame_index: 0,
                },
                confornets_confidence: {
                    plddt: 76.3321,
                    gpde: 0.8197,
                    ptm: 0.611,
                },
                confornets_reference_evaluation: {
                    min_reference_rmsd: 6.027059,
                },
                confornets_pairwise_diversity: {
                    mean_pairwise_rmsd: 7.927423,
                },
                confornets_reporting: {
                    sample_semantics: 'independent_generated_conformer_sample',
                },
            },
        } as UntypedApiValue,
    });

    assert.deepEqual(cards.map((card) => [card.label, card.value, card.decimals]), [
        ['ConforNets pLDDT', 76.3321, 1],
        ['ConforNets gPDE', 0.8197, 3],
        ['ConforNets pTM', 0.611, 3],
        ['Staged-reference Cα RMSD', 6.027059, 2],
        ['Pairwise sample RMSD', 7.927423, 2],
    ]);
});

test('pLDDT color map falls back from chain metrics to persisted residue metrics for ConforNets monomers', () => {
    const colorForValue = (value: number) => ({ r: Math.round(value), g: 0, b: 0 });
    const colorMap = buildPlddtResidueColorMap({
        chainMetrics: {},
        plddtProfile: [42, 77],
        residueNumbers: [5, 6],
        fallbackChainId: 'A',
        colorForValue,
    });

    assert.ok(colorMap);
    assert.deepEqual(Array.from(colorMap.entries()), [
        ['A:5', { r: 42, g: 0, b: 0 }],
        ['A:6', { r: 77, g: 0, b: 0 }],
    ]);
});

test('pLDDT color map prefers chain-resolved metrics when available', () => {
    const colorForValue = (value: number) => ({ r: Math.round(value), g: 1, b: 2 });
    const colorMap = buildPlddtResidueColorMap({
        chainMetrics: {
            B: {
                plddt: [91, 64],
                residue_numbers: [10, 11],
            },
        },
        plddtProfile: [42, 77],
        residueNumbers: [5, 6],
        fallbackChainId: 'A',
        colorForValue,
    });

    assert.ok(colorMap);
    assert.deepEqual(Array.from(colorMap.entries()), [
        ['B:10', { r: 91, g: 1, b: 2 }],
        ['B:11', { r: 64, g: 1, b: 2 }],
    ]);
});

test('ConforNets chain id is extracted from request metadata for residue color fallback', () => {
    assert.equal(
        getConforNetsDefaultChainId({
            confidence_metrics: {
                confornets_request: {
                    chain_id: 'B',
                },
            },
        } as UntypedApiValue),
        'B',
    );
    assert.equal(
        getConforNetsDefaultChainId({
            provenance: {
                confornets_provenance: {
                    request: {
                        chain_id: 'C',
                    },
                },
            },
        } as UntypedApiValue),
        'C',
    );
});

test('ConforNets scalar pLDDT can drive a uniform residue color fallback when per-residue tensors are absent', () => {
    const selectedDesign = {
        confidence_metrics: {
            confornets_sample: {
                confidence: {
                    plddt: 76.3321,
                },
            },
            confornets_artifact_manifest: {
                full_confidence_tensor_count: 0,
            },
        },
    } as UntypedApiValue;
    const colorForValue = (value: number) => ({ r: Math.round(value), g: 0, b: 0 });

    assert.equal(getConforNetsScalarPlddt(selectedDesign), 76.3321);

    const colorMap = buildPlddtResidueColorMap({
        chainMetrics: {
            A: {
                plddt: [50, 50],
                residue_numbers: [1, 2],
            },
        },
        plddtProfile: [50, 50],
        residueNumbers: [1, 2],
        fallbackChainId: 'A',
        scalarPlddtFallback: getConforNetsScalarPlddt(selectedDesign),
        preferScalarFallback: true,
        colorForValue,
    });

    assert.ok(colorMap);
    assert.deepEqual(Array.from(colorMap.entries()), [
        ['A:1', { r: 76, g: 0, b: 0 }],
        ['A:2', { r: 76, g: 0, b: 0 }],
    ]);
});

test('ConforNets scalar pLDDT fallback is not forced when full per-residue confidence tensors exist', () => {
    const selectedDesign = {
        confidence_metrics: {
            confornets_confidence: { plddt: 81.5 },
            confornets_artifact_manifest: {
                full_confidence_tensor_count: 4,
            },
        },
    } as UntypedApiValue;
    const colorForValue = (value: number) => ({ r: Math.round(value), g: 0, b: 0 });

    const colorMap = buildPlddtResidueColorMap({
        chainMetrics: {
            B: {
                plddt: [51, 82],
                residue_numbers: [10, 11],
            },
        },
        plddtProfile: [],
        residueNumbers: [],
        fallbackChainId: 'B',
        scalarPlddtFallback: getConforNetsScalarPlddt(selectedDesign),
        preferScalarFallback: false,
        colorForValue,
    });

    assert.ok(colorMap);
    assert.deepEqual(Array.from(colorMap.entries()), [
        ['B:10', { r: 51, g: 0, b: 0 }],
        ['B:11', { r: 82, g: 0, b: 0 }],
    ]);
});

test('ConforNets detection uses artifact, provenance, and nested sample metadata', () => {
    assert.equal(isConforNetsDesign({ artifact_group: 'confornets' } as UntypedApiValue), true);
    assert.equal(isConforNetsDesign({ provenance: { artifact_group: 'confornets' } } as UntypedApiValue), true);
    assert.equal(isConforNetsDesign({ provenance: { model_id: 'confornets_experimental' } } as UntypedApiValue), true);
    assert.equal(isConforNetsDesign({ confidence_metrics: { confornets_sample: { frame_index: 4 } } } as UntypedApiValue), true);
    assert.equal(isConforNetsDesign({ name: 'cn_00009_sample_9' } as UntypedApiValue), true);
    assert.equal(isConforNetsDesign({ name: 'variant_001_model_0' } as UntypedApiValue), false);
});

test('ConforNets sample index prefers explicit frame metadata over name parsing', () => {
    assert.equal(
        getConforNetsSampleIndex({
            name: 'cn_00009_sample_9',
            confidence_metrics: { confornets_sample: { frame_index: 3 } },
        } as UntypedApiValue),
        3,
    );
    assert.equal(getConforNetsSampleIndex({ name: 'cn_00009_sample_9' } as UntypedApiValue), 9);
    assert.equal(getConforNetsSampleIndex({ name: 'other' } as UntypedApiValue), null);
});

test('ConforNets conformer navigation exposes slider and step targets', () => {
    const conformerSet = buildConforNetsConformerSet([
        { id: 'sample-0', job_id: 'job-a', name: 'cn_00000_sample_0', artifact_group: 'confornets' },
        { id: 'sample-1', job_id: 'job-a', name: 'cn_00001_sample_1', artifact_group: 'confornets' },
        { id: 'sample-2', job_id: 'job-a', name: 'cn_00002_sample_2', artifact_group: 'confornets' },
    ] as UntypedApiValue[], 'sample-1');

    assert.ok(conformerSet);
    const navigation = buildConforNetsConformerNavigation(conformerSet);

    assert.deepEqual(navigation, {
        totalCount: 3,
        selectedNumber: 2,
        selectedId: 'sample-1',
        previousId: 'sample-0',
        nextId: 'sample-2',
        sliderMin: 0,
        sliderMax: 2,
        sliderValue: 1,
        selectedLabel: 'Frame 1 • cn_00001_sample_1',
    });
});

test('ConforNets overlay ids are de-duplicated, valid, sorted, and never include the active conformer', () => {
    const conformerSet = buildConforNetsConformerSet([
        { id: 'sample-0', job_id: 'job-a', name: 'cn_00000_sample_0', artifact_group: 'confornets' },
        { id: 'sample-1', job_id: 'job-a', name: 'cn_00001_sample_1', artifact_group: 'confornets' },
        { id: 'sample-2', job_id: 'job-a', name: 'cn_00002_sample_2', artifact_group: 'confornets' },
    ] as UntypedApiValue[], 'sample-1');

    assert.ok(conformerSet);
    assert.deepEqual(
        resolveConforNetsOverlayIds(conformerSet, ['sample-2', 'sample-1', 'missing', 'sample-0', 'sample-2']),
        ['sample-0', 'sample-2'],
    );
});

test('StructureViewerPane wires first-class ConforNets slider, step, and overlay controls', () => {
    const source = readFileSync('src/components/StructureViewerPane.tsx', 'utf8');
    const molstarSource = readFileSync('src/components/MolstarViewer.tsx', 'utf8');

    assert.match(source, /data-confornets-conformer-controls/);
    assert.match(source, /type="range"/);
    assert.match(source, /Previous conformation/);
    assert.match(source, /Next conformation/);
    assert.match(source, /Overlay conformers/);
    assert.match(source, /overlayStructures=/);
    assert.match(source, /setFocusedMetricSection\(quickView\.sectionId\)/);
    assert.match(source, /setOverlayView\(quickView\.overlayView\)/);
    assert.match(source, /setColorMode\(quickView\.colorMode\)/);
    assert.match(source, /buildPlddtResidueColorMap/);
    assert.match(source, /getConforNetsScalarPlddt/);
    assert.match(source, /residueNumbers:\s*residueMetricNumbers/);
    assert.match(source, /fallbackChainId:\s*conforNetsDefaultChainId/);
    assert.match(source, /scalarPlddtFallback:\s*conforNetsScalarPlddt/);
    assert.match(source, /preferScalarFallback:\s*conforNetsUsesScalarPlddtFallback/);
    assert.match(source, /overlay ready: \$\{bfactorLabel\} residue\/chain map/);
    assert.match(source, /Uniform scalar pLDDT/);
    assert.match(source, /residueColors=\{/);
    assert.match(source, /plddtResidueColors/);
    assert.match(molstarSource, /overlayStructures/);
    assert.match(molstarSource, /viewerInstance\.load/);
    assert.match(molstarSource, /fullLoad:\s*false/);
    assert.match(molstarSource, /alphafold-view/);
    assert.match(molstarSource, /residueColors && residueColors\.size > 0 \? 'false'/);
});
