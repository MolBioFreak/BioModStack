import type { Design } from '../lib/api.js';
import type { AnalysisLens } from './designOutputSource.js';

export type StructureViewerSectionId = 'summary' | 'confidence' | 'interface' | 'geometry' | 'designability';

export interface StructureViewerConfidenceSemantics {
    shortLabel: string;
    headlineLabel: string;
    profileTitle: string;
    legendHeading: string;
    preferChainColoring: boolean;
}

export interface StructureViewerSection {
    id: StructureViewerSectionId;
    label: string;
}

export interface StructureViewerSummaryCardSpec {
    label: string;
    value: number | null;
    decimals: number;
    suffix?: string;
    accentField?: string;
    accentClass?: string;
}

export type StructureViewerOverlayView = 'metrics' | 'plddt' | 'psce' | 'pae';
export type StructureViewerColorMode = 'default' | 'plddt' | 'cdr' | 'frustration' | 'fampnn_psce';

export interface StructureViewerQuickViewSpec {
    id: StructureViewerSectionId | 'cdr';
    label: string;
    sectionId: StructureViewerSectionId;
    overlayView: StructureViewerOverlayView;
    colorMode: StructureViewerColorMode;
}

type SummaryMetricDesign = Partial<Pick<Design,
    | 'plddt_overall'
    | 'plddt_binder'
    | 'plddt_target'
    | 'pae_overall'
    | 'pae_interaction'
    | 'ptm'
    | 'iptm'
    | 'ipsae'
    | 'complex_iplddt'
    | 'complex_ipde'
>>;

interface ConfidenceSemanticsInput {
    activeJobModelId?: string | null;
    designLens?: AnalysisLens | null;
}

interface SectionInput {
    hasResidueConfidence: boolean;
    hasPaeMatrix: boolean;
    hasStructureSummary: boolean;
    hasIpsaeInterface: boolean;
    hasChainPairIptm: boolean;
    hasContactMap: boolean;
    hasFampnnDesign?: boolean;
    hasFampnnPsceProfile: boolean;
    hasFrustrationSummary: boolean;
}

interface QuickViewsInput extends SectionInput {
    confidenceLabel: string;
    hasCdrOverlay: boolean;
}

interface SummaryCardsInput {
    confidenceLabel: string;
    designLens?: AnalysisLens | null;
    selectedDesign: SummaryMetricDesign | null | undefined;
}

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export const resolveStructureViewerConfidenceSemantics = ({
    activeJobModelId,
    designLens,
}: ConfidenceSemanticsInput): StructureViewerConfidenceSemantics => {
    const normalizedModelId = String(activeJobModelId || '').toLowerCase();
    const isOligoJob = normalizedModelId.includes('oligo');

    if (isOligoJob) {
        return {
            shortLabel: 'Design Conf.',
            headlineLabel: 'Design Confidence',
            profileTitle: 'Design Confidence Profile',
            legendHeading: 'Design-confidence bands',
            preferChainColoring: true,
        };
    }

    if (designLens === 'rfantibody') {
        return {
            shortLabel: 'RF pLDDT',
            headlineLabel: 'RF pLDDT',
            profileTitle: 'RF pLDDT Profile',
            legendHeading: 'RF confidence bands',
            preferChainColoring: false,
        };
    }

    return {
        shortLabel: 'pLDDT',
        headlineLabel: 'pLDDT',
        profileTitle: 'pLDDT Profile',
        legendHeading: 'pLDDT confidence bands',
        preferChainColoring: false,
    };
};

export const buildStructureViewerSections = ({
    hasResidueConfidence,
    hasPaeMatrix,
    hasStructureSummary,
    hasIpsaeInterface,
    hasChainPairIptm,
    hasContactMap,
    hasFampnnDesign = false,
    hasFampnnPsceProfile,
    hasFrustrationSummary,
}: SectionInput): StructureViewerSection[] => {
    const sections: StructureViewerSection[] = [
        { id: 'summary', label: 'Summary' },
    ];

    if (hasResidueConfidence) {
        sections.push({ id: 'confidence', label: 'Confidence' });
    }

    if (hasIpsaeInterface || hasChainPairIptm) {
        sections.push({ id: 'interface', label: 'Interface' });
    }

    if (hasStructureSummary || hasPaeMatrix || hasContactMap) {
        sections.push({ id: 'geometry', label: 'Geometry' });
    }

    if (hasFampnnDesign || hasFampnnPsceProfile || hasFrustrationSummary) {
        sections.push({ id: 'designability', label: 'Designability' });
    }

    return sections;
};

export const buildStructureViewerQuickViews = ({
    confidenceLabel,
    hasResidueConfidence,
    hasPaeMatrix,
    hasStructureSummary,
    hasIpsaeInterface,
    hasChainPairIptm,
    hasContactMap,
    hasFampnnDesign = false,
    hasFampnnPsceProfile,
    hasFrustrationSummary,
    hasCdrOverlay,
}: QuickViewsInput): StructureViewerQuickViewSpec[] => {
    const quickViews: StructureViewerQuickViewSpec[] = [
        {
            id: 'summary',
            label: 'Summary',
            sectionId: 'summary',
            overlayView: 'metrics',
            colorMode: 'default',
        },
    ];

    if (hasResidueConfidence) {
        quickViews.push({
            id: 'confidence',
            label: confidenceLabel,
            sectionId: 'confidence',
            overlayView: 'plddt',
            colorMode: 'plddt',
        });
    }

    if (hasIpsaeInterface || hasChainPairIptm) {
        quickViews.push({
            id: 'interface',
            label: 'Interface',
            sectionId: 'interface',
            overlayView: 'metrics',
            colorMode: 'default',
        });
    }

    if (hasStructureSummary || hasPaeMatrix || hasContactMap) {
        quickViews.push({
            id: 'geometry',
            label: 'Geometry',
            sectionId: 'geometry',
            overlayView: hasPaeMatrix ? 'pae' : 'metrics',
            colorMode: 'default',
        });
    }

    if (hasFampnnDesign || hasFampnnPsceProfile || hasFrustrationSummary) {
        quickViews.push({
            id: 'designability',
            label: 'Designability',
            sectionId: 'designability',
            overlayView: hasFampnnPsceProfile ? 'psce' : 'metrics',
            colorMode: hasFampnnPsceProfile ? 'fampnn_psce' : hasFrustrationSummary ? 'frustration' : 'default',
        });
    }

    if (hasCdrOverlay) {
        quickViews.push({
            id: 'cdr',
            label: 'CDR',
            sectionId: 'confidence',
            overlayView: 'metrics',
            colorMode: 'cdr',
        });
    }

    return quickViews;
};

export const buildStructureViewerSummaryCards = ({
    confidenceLabel,
    selectedDesign,
}: SummaryCardsInput): StructureViewerSummaryCardSpec[] => {
    if (!selectedDesign) return [];

    const cards: StructureViewerSummaryCardSpec[] = [
        {
            label: confidenceLabel,
            value: selectedDesign.plddt_overall ?? null,
            decimals: 1,
            accentField: 'plddt_overall',
        },
    ];

    if (isFiniteNumber(selectedDesign.plddt_binder)) {
        cards.push({
            label: 'Binder pLDDT',
            value: selectedDesign.plddt_binder,
            decimals: 1,
            accentField: 'plddt_binder',
        });
    }

    if (isFiniteNumber(selectedDesign.plddt_target)) {
        cards.push({
            label: 'Target pLDDT',
            value: selectedDesign.plddt_target,
            decimals: 1,
            accentField: 'plddt_target',
        });
    }

    cards.push({
        label: 'PAE',
        value: selectedDesign.pae_overall ?? null,
        decimals: 2,
        accentField: 'pae_overall',
    });

    if (isFiniteNumber(selectedDesign.pae_interaction)) {
        cards.push({
            label: 'Interaction PAE',
            value: selectedDesign.pae_interaction,
            decimals: 2,
            accentField: 'pae_interaction',
        });
    }

    cards.push(
        {
            label: 'pTM',
            value: selectedDesign.ptm ?? null,
            decimals: 3,
            accentClass: 'text-violet-400',
        },
        {
            label: 'iPTM',
            value: selectedDesign.iptm ?? null,
            decimals: 3,
            accentClass: 'text-amber-400',
        },
        {
            label: 'ipSAE',
            value: selectedDesign.ipsae ?? null,
            decimals: 3,
            accentField: 'ipsae',
        },
    );

    if (isFiniteNumber(selectedDesign.complex_iplddt)) {
        cards.push({
            label: 'Complex iPLDDT',
            value: selectedDesign.complex_iplddt,
            decimals: 3,
            accentClass: 'text-cyan-300',
        });
    }

    if (isFiniteNumber(selectedDesign.complex_ipde)) {
        cards.push({
            label: 'Interface PDE',
            value: selectedDesign.complex_ipde,
            decimals: 2,
            accentClass: 'text-rose-300',
        });
    }

    return cards;
};
