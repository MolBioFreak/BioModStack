import type { Design } from './api.js';

export type ResultAnalyzerId = string;

export type ViewerCapability =
    | 'result_filter'
    | 'structure_viewer'
    | 'antibody_backbone_metrics'
    | 'sequence_design_metrics'
    | 'ppiflow_maturation_metrics'
    | 'structure_confidence_metrics'
    | 'complex_interface_metrics'
    | 'de_novo_generation_metrics'
    | 'generic_metadata'
    | string;

export type ReviewTabId =
    | 'overview'
    | 'charts'
    | 'structure'
    | 'antibody'
    | 'table'
    | 'compare_designs'
    | 'compare';

export type ResultCapabilityDesign = Pick<
    Design,
    | 'analysis_contract_id'
    | 'supported_analyzers'
    | 'viewer_capabilities'
    | 'result_contract_source'
    | 'review_artifact_manifest'
>;

function stringList(value: unknown): string[] {
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function artifactReady(design: Partial<ResultCapabilityDesign> | null | undefined, artifact: string): boolean {
    const manifest = design?.review_artifact_manifest;
    if (!manifest || manifest.schema !== 'bms.review-artifacts.v1') {
        // Applicability and material readiness are separate. A missing or
        // invalid typed manifest cannot make an artifact-backed feature ready.
        return false;
    }
    return manifest.artifacts?.[artifact]?.state === 'ready';
}

export function supportsAnalyzer(design: Partial<ResultCapabilityDesign> | null | undefined, analyzer: ResultAnalyzerId): boolean {
    return stringList(design?.supported_analyzers).includes(analyzer);
}

const ANALYZER_REQUIRED_ARTIFACTS: Record<string, string[]> = {
    structure_summary: ['structure'],
    contact_map: ['structure'],
    chain_metrics: ['structure'],
    fampnn_psce_profile: ['structure'],
    antibody_annotation_pack: ['structure'],
    pae_matrix: ['aligned_error'],
    ipsae_interface: ['structure', 'aligned_error'],
};

export function isAnalyzerAvailable(
    design: Partial<ResultCapabilityDesign> | null | undefined,
    analyzer: ResultAnalyzerId,
): boolean {
    if (!supportsAnalyzer(design, analyzer)) return false;
    return (ANALYZER_REQUIRED_ARTIFACTS[analyzer] ?? []).every((artifact) => artifactReady(design, artifact));
}

export function supportsViewerCapability(design: Partial<ResultCapabilityDesign> | null | undefined, capability: ViewerCapability): boolean {
    const declared = stringList(design?.viewer_capabilities).includes(capability);
    if (!declared) return false;
    if (capability === 'structure_viewer') return artifactReady(design, 'structure');
    if (capability === 'structure_confidence_metrics') {
        return artifactReady(design, 'structure') || artifactReady(design, 'aligned_error');
    }
    return true;
}

export function isUnsupportedResult(design: Partial<ResultCapabilityDesign> | null | undefined): boolean {
    return !design?.analysis_contract_id || stringList(design.viewer_capabilities).length === 0;
}

export function getUnsupportedResultReason(design: Partial<ResultCapabilityDesign> | null | undefined): string {
    if (!design?.analysis_contract_id) {
        return 'Unsupported result: no analysis contract is declared for this model/result row.';
    }
    if (stringList(design.viewer_capabilities).length === 0) {
        return 'Unsupported result: contract has no enabled review capabilities yet.';
    }
    return 'Supported result.';
}

export function getVisibleReviewTabs(design: Partial<ResultCapabilityDesign> | null | undefined): ReviewTabId[] {
    if (isUnsupportedResult(design)) return ['overview', 'table'];
    const tabs: ReviewTabId[] = ['overview', 'charts'];
    if (supportsViewerCapability(design, 'structure_viewer')) tabs.push('structure');
    if (supportsViewerCapability(design, 'antibody_backbone_metrics')) tabs.push('antibody');
    tabs.push('table', 'compare_designs', 'compare');
    return tabs;
}

export function getReviewColumnCapabilities(design: Partial<ResultCapabilityDesign> | null | undefined): {
    antibody: boolean;
    interface: boolean;
    sequenceDesign: boolean;
} {
    const antibody = supportsViewerCapability(design, 'antibody_backbone_metrics')
        || supportsViewerCapability(design, 'ppiflow_maturation_metrics');
    return {
        antibody,
        interface: antibody || supportsViewerCapability(design, 'complex_interface_metrics'),
        sequenceDesign: supportsViewerCapability(design, 'sequence_design_metrics'),
    };
}
