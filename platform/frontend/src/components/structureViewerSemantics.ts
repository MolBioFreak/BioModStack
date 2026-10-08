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

export interface EffectiveStructureViewerColorModeInput {
    requestedMode: StructureViewerColorMode;
    hasResidueConfidence: boolean;
    hasFampnnPsceProfile: boolean;
    hasFrustrationResidues: boolean;
}

export const resolveEffectiveStructureViewerColorMode = ({
    requestedMode,
    hasResidueConfidence,
    hasFampnnPsceProfile,
    hasFrustrationResidues,
}: EffectiveStructureViewerColorModeInput): StructureViewerColorMode => {
    if (requestedMode === 'plddt' && !hasResidueConfidence) return 'default';
    if (requestedMode === 'fampnn_psce' && !hasFampnnPsceProfile) return 'default';
    if (requestedMode === 'frustration' && !hasFrustrationResidues) return 'default';
    return requestedMode;
};

export interface StructureViewerQuickViewSpec {
    id: StructureViewerSectionId | 'cdr';
    label: string;
    sectionId: StructureViewerSectionId;
    overlayView: StructureViewerOverlayView;
    colorMode: StructureViewerColorMode;
}

type SummaryMetricDesign = Partial<Design>;

interface ChainMetricLike {
    plddt?: number[] | null;
    residue_numbers?: number[] | null;
    length?: number | null;
}

export interface StructureViewerResidueColor {
    r: number;
    g: number;
    b: number;
}

export interface PlddtResidueMaskPoint {
    chain_id: string;
    residue_number: number;
}

export interface PlddtResidueColorMapInput {
    chainMetrics?: Record<string, ChainMetricLike | null | undefined> | null;
    plddtProfile?: number[] | null;
    residueNumbers?: number[] | null;
    fallbackChainId?: string | null;
    scalarPlddtFallback?: number | null;
    preferScalarFallback?: boolean;
    residueMask?: PlddtResidueMaskPoint[] | null;
    maskMode?: 'include_only' | 'none';
    colorForValue: (value: number) => StructureViewerResidueColor;
}

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

const asRecord = (value: unknown): Record<string, unknown> | null => (
    value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
);

const normalizeToken = (value: unknown): string => String(value ?? '').trim().toLowerCase();

const finiteNumericValue = (value: unknown): number | null => {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
};

export interface ConforNetsConformerSummary {
    id: string;
    name: string;
    frameIndex: number | null;
    design: Design;
}

export interface ConforNetsConformerSet {
    selectedIndex: number;
    selectedConformer: ConforNetsConformerSummary;
    conformers: ConforNetsConformerSummary[];
}

export interface ConforNetsConformerNavigation {
    totalCount: number;
    selectedNumber: number;
    selectedId: string;
    previousId: string | null;
    nextId: string | null;
    sliderMin: number;
    sliderMax: number;
    sliderValue: number;
    selectedLabel: string;
}

export const isConforNetsDesign = (design: Partial<Design> | null | undefined): boolean => {
    if (!design) return false;
    const provenance = asRecord(design.provenance);
    const confidenceMetrics = asRecord(design.confidence_metrics);
    const provenanceModelId = normalizeToken(provenance?.model_id);
    const provenanceArtifactGroup = normalizeToken(provenance?.artifact_group);
    const provenanceStageFamily = normalizeToken(provenance?.stage_family);
    const topLevelModelId = normalizeToken((design as Partial<Design> & { model_id?: unknown }).model_id);

    return normalizeToken(design.artifact_group) === 'confornets'
        || normalizeToken(design.stage_family) === 'confornets'
        || provenanceArtifactGroup === 'confornets'
        || provenanceStageFamily === 'confornets'
        || provenanceModelId.includes('confornets')
        || topLevelModelId.includes('confornets')
        || Boolean(asRecord(confidenceMetrics?.confornets_sample))
        || Boolean(asRecord(confidenceMetrics?.confornets_ensemble))
        || Boolean(asRecord(confidenceMetrics?.confornets_artifact_manifest))
        || /^cn_\d+_sample_\d+$/i.test(String(design.name ?? ''));
};

const sanitizeChainId = (value: unknown): string | null => {
    const normalized = String(value ?? '').trim();
    return normalized ? normalized : null;
};

export const getConforNetsDefaultChainId = (design: Partial<Design> | null | undefined): string | null => {
    if (!design) return null;
    const confidenceMetrics = asRecord(design.confidence_metrics);
    const request = asRecord(confidenceMetrics?.confornets_request);
    const provenance = asRecord(design.provenance);
    const confornetsProvenance = asRecord(provenance?.confornets_provenance);
    const provenanceRequest = asRecord(confornetsProvenance?.request);

    return sanitizeChainId(request?.chain_id)
        ?? sanitizeChainId(provenanceRequest?.chain_id);
};

export const getConforNetsScalarPlddt = (design: Partial<Design> | null | undefined): number | null => {
    if (!design || !isConforNetsDesign(design)) return null;
    const confidenceMetrics = asRecord(design.confidence_metrics);
    const sample = asRecord(confidenceMetrics?.confornets_sample);
    const sampleConfidence = asRecord(sample?.confidence);
    const confidence = asRecord(confidenceMetrics?.confornets_confidence);

    return finiteNumericValue(sampleConfidence?.plddt)
        ?? finiteNumericValue(confidence?.plddt)
        ?? finiteNumericValue(design.plddt_overall);
};

const addUniformScalarResidueColors = (
    colorMap: Map<string, StructureViewerResidueColor>,
    input: Omit<PlddtResidueColorMapInput, 'colorForValue'>,
    scalarPlddt: number,
    colorForValue: (value: number) => StructureViewerResidueColor,
): void => {
    const scalarColor = colorForValue(scalarPlddt);
    for (const [rawChainId, metric] of Object.entries(input.chainMetrics ?? {})) {
        const chainId = sanitizeChainId(rawChainId);
        if (!chainId) continue;
        const plddt = Array.isArray(metric?.plddt) ? metric.plddt : [];
        const residueNumbers = Array.isArray(metric?.residue_numbers) ? metric.residue_numbers : [];
        const metricLength = isFiniteNumber(metric?.length) ? metric.length : 0;
        const count = Math.max(plddt.length, residueNumbers.length, metricLength);
        for (let idx = 0; idx < count; idx++) {
            const residueNumber = residueNumbers[idx] ?? (idx + 1);
            if (!Number.isFinite(residueNumber)) continue;
            colorMap.set(`${chainId}:${residueNumber}`, scalarColor);
        }
    }
    if (colorMap.size > 0) return;

    const fallbackChainId = sanitizeChainId(input.fallbackChainId);
    if (!fallbackChainId) return;
    const profile = Array.isArray(input.plddtProfile) ? input.plddtProfile : [];
    const residueNumbers = Array.isArray(input.residueNumbers) ? input.residueNumbers : [];
    const count = Math.max(profile.length, residueNumbers.length);
    for (let idx = 0; idx < count; idx++) {
        const residueNumber = residueNumbers[idx] ?? (idx + 1);
        if (!Number.isFinite(residueNumber)) continue;
        colorMap.set(`${fallbackChainId}:${residueNumber}`, scalarColor);
    }
};

export const buildPlddtResidueColorMap = ({
    chainMetrics,
    plddtProfile,
    residueNumbers,
    fallbackChainId,
    scalarPlddtFallback,
    preferScalarFallback,
    residueMask,
    maskMode = 'none',
    colorForValue,
}: PlddtResidueColorMapInput): Map<string, StructureViewerResidueColor> | undefined => {
    const colorMap = new Map<string, StructureViewerResidueColor>();
    const residueMaskKeys = maskMode === 'include_only' && Array.isArray(residueMask) && residueMask.length > 0
        ? new Set(residueMask.map((point) => `${String(point.chain_id).trim()}:${point.residue_number}`))
        : null;
    const shouldIncludeResidue = (chainId: string, residueNumber: number): boolean => (
        !residueMaskKeys || residueMaskKeys.has(`${chainId}:${residueNumber}`)
    );
    const scalarFallback = finiteNumericValue(scalarPlddtFallback);
    if (preferScalarFallback && scalarFallback !== null) {
        addUniformScalarResidueColors(
            colorMap,
            { chainMetrics, plddtProfile, residueNumbers, fallbackChainId, scalarPlddtFallback, preferScalarFallback },
            scalarFallback,
            colorForValue,
        );
        if (residueMaskKeys) {
            for (const key of Array.from(colorMap.keys())) {
                if (!residueMaskKeys.has(key)) colorMap.delete(key);
            }
        }
        if (colorMap.size > 0) return colorMap;
    }

    for (const [chainId, metric] of Object.entries(chainMetrics ?? {})) {
        const plddt = Array.isArray(metric?.plddt) ? metric.plddt : [];
        if (!plddt.length) continue;
        const chainResidueNumbers = Array.isArray(metric?.residue_numbers) ? metric.residue_numbers : [];
        for (let idx = 0; idx < plddt.length; idx++) {
            const value = plddt[idx];
            if (!isFiniteNumber(value)) continue;
            const residueNumber = chainResidueNumbers[idx] ?? (idx + 1);
            if (!Number.isFinite(residueNumber) || !shouldIncludeResidue(chainId, residueNumber)) continue;
            colorMap.set(`${chainId}:${residueNumber}`, colorForValue(value));
        }
    }
    if (colorMap.size > 0) return colorMap;

    const chainId = sanitizeChainId(fallbackChainId);
    const profile = Array.isArray(plddtProfile) ? plddtProfile : [];
    if (!chainId || profile.length === 0) return undefined;
    const numbers = Array.isArray(residueNumbers) ? residueNumbers : [];
    for (let idx = 0; idx < profile.length; idx++) {
        const value = profile[idx];
        if (!isFiniteNumber(value)) continue;
        const residueNumber = numbers[idx] ?? (idx + 1);
        if (!Number.isFinite(residueNumber) || !shouldIncludeResidue(chainId, residueNumber)) continue;
        colorMap.set(`${chainId}:${residueNumber}`, colorForValue(value));
    }
    return colorMap.size > 0 ? colorMap : undefined;
};

export const getConforNetsSampleIndex = (design: Partial<Design> | null | undefined): number | null => {
    if (!design) return null;
    const confidenceMetrics = asRecord(design.confidence_metrics);
    const sample = asRecord(confidenceMetrics?.confornets_sample);
    const ensemble = asRecord(confidenceMetrics?.confornets_ensemble);
    const explicitIndex = finiteNumericValue(sample?.frame_index)
        ?? finiteNumericValue(sample?.sample_index)
        ?? finiteNumericValue(ensemble?.frame_index)
        ?? finiteNumericValue(ensemble?.sample_index);
    if (explicitIndex !== null) return explicitIndex;

    const nameMatch = String(design.name ?? '').match(/^cn_(\d+)_sample_(\d+)$/i);
    if (!nameMatch) return null;
    return Number.parseInt(nameMatch[2] ?? nameMatch[1], 10);
};

export const buildConforNetsConformerSet = (
    designs: Design[],
    selectedDesignId: string | null | undefined,
): ConforNetsConformerSet | null => {
    if (!selectedDesignId) return null;
    const selectedDesign = designs.find((design) => design.id === selectedDesignId);
    if (!selectedDesign || !isConforNetsDesign(selectedDesign)) return null;

    const conformers = designs
        .filter((design) => design.job_id === selectedDesign.job_id && isConforNetsDesign(design))
        .map((design): ConforNetsConformerSummary => ({
            id: design.id,
            name: design.name,
            frameIndex: getConforNetsSampleIndex(design),
            design,
        }))
        .sort((a, b) => {
            if (a.frameIndex !== null && b.frameIndex !== null && a.frameIndex !== b.frameIndex) {
                return a.frameIndex - b.frameIndex;
            }
            if (a.frameIndex !== null && b.frameIndex === null) return -1;
            if (a.frameIndex === null && b.frameIndex !== null) return 1;
            return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
        });

    const selectedIndex = conformers.findIndex((conformer) => conformer.id === selectedDesignId);
    if (selectedIndex < 0) return null;
    return {
        selectedIndex,
        selectedConformer: conformers[selectedIndex],
        conformers,
    };
};

const formatConforNetsConformerLabel = (conformer: ConforNetsConformerSummary): string => {
    const frameLabel = conformer.frameIndex === null ? 'Frame ?' : `Frame ${conformer.frameIndex}`;
    return `${frameLabel} • ${conformer.name}`;
};

export const buildConforNetsConformerNavigation = (
    conformerSet: ConforNetsConformerSet | null | undefined,
): ConforNetsConformerNavigation | null => {
    if (!conformerSet || conformerSet.conformers.length === 0) return null;
    const lastIndex = conformerSet.conformers.length - 1;
    const selectedIndex = Math.min(Math.max(conformerSet.selectedIndex, 0), lastIndex);
    const selectedConformer = conformerSet.conformers[selectedIndex];
    return {
        totalCount: conformerSet.conformers.length,
        selectedNumber: selectedIndex + 1,
        selectedId: selectedConformer.id,
        previousId: selectedIndex > 0 ? conformerSet.conformers[selectedIndex - 1].id : null,
        nextId: selectedIndex < lastIndex ? conformerSet.conformers[selectedIndex + 1].id : null,
        sliderMin: 0,
        sliderMax: lastIndex,
        sliderValue: selectedIndex,
        selectedLabel: formatConforNetsConformerLabel(selectedConformer),
    };
};

export const resolveConforNetsOverlayIds = (
    conformerSet: ConforNetsConformerSet | null | undefined,
    requestedIds: readonly string[],
): string[] => {
    if (!conformerSet) return [];
    const requested = new Set(requestedIds);
    requested.delete(conformerSet.selectedConformer.id);
    return conformerSet.conformers
        .map((conformer) => conformer.id)
        .filter((id) => requested.has(id));
};

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

const buildConforNetsSummaryCards = (selectedDesign: SummaryMetricDesign): StructureViewerSummaryCardSpec[] => {
    if (!isConforNetsDesign(selectedDesign)) return [];
    const confidenceMetrics = asRecord(selectedDesign.confidence_metrics);
    const sample = asRecord(confidenceMetrics?.confornets_sample);
    const confidence = asRecord(confidenceMetrics?.confornets_confidence) ?? asRecord(sample?.confidence);
    const referenceEvaluation = asRecord(confidenceMetrics?.confornets_reference_evaluation) ?? asRecord(sample?.reference_evaluation);
    const pairwiseDiversity = asRecord(confidenceMetrics?.confornets_pairwise_diversity) ?? asRecord(sample?.pairwise_diversity);

    const cards: StructureViewerSummaryCardSpec[] = [];
    const scalarPlddt = finiteNumericValue(confidence?.plddt) ?? finiteNumericValue(selectedDesign.plddt_overall);
    if (scalarPlddt !== null) {
        cards.push({
            label: 'ConforNets pLDDT',
            value: scalarPlddt,
            decimals: 1,
            accentField: 'plddt_overall',
        });
    }

    const gpde = finiteNumericValue(confidence?.gpde);
    if (gpde !== null) {
        cards.push({
            label: 'ConforNets gPDE',
            value: gpde,
            decimals: 3,
            accentClass: 'text-cyan-300',
        });
    }

    const ptm = finiteNumericValue(confidence?.ptm) ?? finiteNumericValue(selectedDesign.ptm);
    if (ptm !== null) {
        cards.push({
            label: 'ConforNets pTM',
            value: ptm,
            decimals: 3,
            accentClass: 'text-violet-400',
        });
    }

    const minReferenceRmsd = finiteNumericValue(referenceEvaluation?.min_reference_rmsd);
    if (minReferenceRmsd !== null) {
        cards.push({
            label: 'Staged-reference Cα RMSD',
            value: minReferenceRmsd,
            decimals: 2,
            suffix: ' Å',
            accentClass: 'text-emerald-300',
        });
    }

    const meanPairwiseRmsd = finiteNumericValue(pairwiseDiversity?.mean_pairwise_rmsd);
    if (meanPairwiseRmsd !== null) {
        cards.push({
            label: 'Pairwise sample RMSD',
            value: meanPairwiseRmsd,
            decimals: 2,
            suffix: ' Å',
            accentClass: 'text-fuchsia-300',
        });
    }

    return cards;
};

export const buildStructureViewerSummaryCards = ({
    confidenceLabel,
    selectedDesign,
}: SummaryCardsInput): StructureViewerSummaryCardSpec[] => {
    if (!selectedDesign) return [];

    const conforNetsCards = buildConforNetsSummaryCards(selectedDesign);
    if (conforNetsCards.length > 0) return conforNetsCards;

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
