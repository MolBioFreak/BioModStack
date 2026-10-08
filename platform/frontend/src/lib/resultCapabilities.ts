import type { Design } from './api.js';

export type ResultAnalyzerId =
    | 'antibody_backbone_v1'
    | 'sequence_design_v1'
    | 'ppiflow_maturation_v1'
    | 'structure_prediction_v1'
    | 'confornets_monomer_v1'
    | string;

export type ViewerCapability =
    | 'result_filter'
    | 'structure_viewer'
    | 'antibody_backbone_metrics'
    | 'sequence_design_metrics'
    | 'ppiflow_maturation_metrics'
    | 'structure_confidence_metrics'
    | 'generic_metadata'
    | string;

const ANALYZER_CAPABILITY_FALLBACKS: Record<string, ViewerCapability[]> = {
    antibody_backbone_v1: ['result_filter', 'structure_viewer', 'antibody_backbone_metrics'],
    sequence_design_v1: ['result_filter', 'structure_viewer', 'sequence_design_metrics'],
    ppiflow_maturation_v1: ['result_filter', 'structure_viewer', 'ppiflow_maturation_metrics'],
    structure_prediction_v1: ['structure_viewer', 'structure_confidence_metrics'],
};

export type ResultCapabilityDesign = Pick<
    Design,
    'analysis_contract_id' | 'supported_analyzers' | 'viewer_capabilities' | 'result_contract_source'
>;

function stringList(value: unknown): string[] {
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function supportsAnalyzer(design: Partial<ResultCapabilityDesign> | null | undefined, analyzer: ResultAnalyzerId): boolean {
    return stringList(design?.supported_analyzers).includes(analyzer);
}

export function supportsViewerCapability(design: Partial<ResultCapabilityDesign> | null | undefined, capability: ViewerCapability): boolean {
    const declaredCapabilities = stringList(design?.viewer_capabilities);
    if (declaredCapabilities.includes(capability)) return true;

    // Compatibility fallback for rows served by older API bundles that already
    // expose analyzer ids but not viewer_capabilities yet. Unknown rows still
    // fail closed because they have no supported_analyzers.
    for (const analyzer of stringList(design?.supported_analyzers)) {
        if ((ANALYZER_CAPABILITY_FALLBACKS[analyzer] || []).includes(capability)) {
            return true;
        }
    }
    return false;
}

export function isUnsupportedResult(design: Partial<ResultCapabilityDesign> | null | undefined): boolean {
    return !design?.analysis_contract_id || stringList(design.supported_analyzers).length === 0;
}

export function getUnsupportedResultReason(design: Partial<ResultCapabilityDesign> | null | undefined): string {
    if (!design?.analysis_contract_id) {
        return 'Unsupported result: no analysis contract is declared for this model/result row.';
    }
    if (stringList(design.supported_analyzers).length === 0) {
        return 'Unsupported result: contract has no enabled analyzers yet.';
    }
    return 'Supported result.';
}
