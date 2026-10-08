import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildStructureViewerQuickViews,
    buildStructureViewerSections,
    buildStructureViewerSummaryCards,
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
