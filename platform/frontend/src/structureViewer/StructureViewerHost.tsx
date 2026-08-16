import { useCallback, useEffect, useMemo, useState } from 'react';

import MolstarViewerImpl from '../components/MolstarViewerImpl';
import type { MolstarViewerProps } from '../components/MolstarViewerImpl';
import { adaptLegacyResidueColors } from './adapters/residueColorSelections';
import type { StructureFilterState, StructurePresentationQuery, StructureScenePresentation } from './contracts/scenePresentation.js';
import { exportMetricIdentity } from './contracts/exportIdentity.js';
import type { StructureComponentType } from './contracts/scenePresentation.js';
import { canonicalResidueRefKey, type ResidueRef } from './contracts/structureIdentity.js';
import type { ViewerMeasurement } from './contracts/measurements.js';
import type { DerivedStructureComponent } from './contracts/complexAnalysis.js';
import { ComplexAnalysisPanel } from './extensions/complex/ComplexAnalysisPanel';
import { FilterPanel } from './extensions/filters/FilterPanel';
import { MetricLegendPanel } from './extensions/metrics/MetricLegendPanel';
import { MeasurementPanel } from './extensions/measurements/MeasurementPanel';
import { M6WorkbenchPanel } from './extensions/m6/M6WorkbenchPanel';
import { PairMatrixExtension } from './extensions/pairMatrix/PairMatrixExtension';
import { ViewerResourceOwner } from './runtime/resourceOwnership.js';
import { SequenceTrackExtension } from './extensions/sequence/SequenceTrackExtension';
import type { MetricLayer, MetricSelection, MetricValue, ResiduePairIdentity } from './metrics/metricContracts.js';
import { MetricRegistry } from './metrics/MetricRegistry.js';
import { projectResidueMetricLayer } from './metrics/metricProjection.js';
import type { StructureSceneController } from './runtime/StructureSceneController.js';

export interface StructureViewerHostProps extends MolstarViewerProps {
    readonly restoredPresentation?: StructureScenePresentation | null;
    readonly metricLayers?: readonly MetricLayer[];
    readonly activeMetricId?: string;
    readonly showMetricWorkbench?: boolean;
    readonly onMetricWorkbenchVisibilityChange?: (visible: boolean) => void;
    readonly showSequenceTrack?: boolean;
    readonly filters?: StructureFilterState;
    readonly onFiltersChange?: (filters: StructureFilterState) => void;
    readonly onMetricSelection?: (selection: MetricSelection) => void;
    readonly residueSelections?: readonly ResidueRef[];
    readonly structureData?: string;
    readonly showMeasurements?: boolean;
    readonly showComplexWorkbench?: boolean;
    readonly derivedComponents?: readonly DerivedStructureComponent[];
    readonly onMeasurementsChange?: (measurements: readonly ViewerMeasurement[]) => void;
    readonly jobId?: string;
    readonly showM6Workbench?: boolean;
}

const EMPTY_METRIC_LAYERS: readonly MetricLayer[] = [];
const ALL_COMPONENT_TYPES: readonly StructureComponentType[] = ['protein', 'dna', 'rna', 'ligand', 'glycan', 'ion', 'water', 'unknown'];
const DEFAULT_FILTERS: StructureFilterState = { includeMissing: false, entityTypes: ALL_COMPONENT_TYPES };

const residueLabel = (residue: ResidueRef): string => `${residue.labelAsymId ?? residue.authAsymId ?? '?'}:${residue.labelSeqId ?? residue.authSeqId ?? '?'}`;

const selectedResidues = (selection: MetricSelection | null): readonly ResidueRef[] => {
    if (!selection) return [];
    return selection.identities.flatMap((identity) => {
        if ('first' in identity && 'second' in identity) {
            const pair = identity as ResiduePairIdentity;
            return [pair.first, pair.second];
        }
        if ('labelSeqId' in identity || 'authSeqId' in identity) return [identity as ResidueRef];
        return [];
    });
};

const filterMetricLayer = (layer: MetricLayer, filters: StructureFilterState): MetricLayer => {
    const chains = new Set(filters.chainIds ?? []);
    const [residueMin, residueMax] = filters.residueRange ?? [-Infinity, Infinity];
    const [metricMin, metricMax] = filters.metricRange ?? [-Infinity, Infinity];
    const residueMatches = (residue: ResidueRef): boolean => {
        const chain = residue.labelAsymId ?? residue.authAsymId ?? '';
        const number = residue.labelSeqId ?? residue.authSeqId;
        return (chains.size === 0 || chains.has(chain))
            && number !== undefined && number >= residueMin && number <= residueMax;
    };
    const valueMatches = (entry: MetricValue<unknown>): boolean => {
        if (entry.missingness !== undefined) return filters.includeMissing ?? false;
        return typeof entry.value === 'number' && entry.value >= metricMin && entry.value <= metricMax;
    };
    if (layer.descriptor.dimension === 'residue-scalar') {
        const values = layer.values as readonly MetricValue<ResidueRef>[];
        return { ...layer, values: values.filter((entry) => residueMatches(entry.identity) && valueMatches(entry)) } as MetricLayer;
    }
    if (layer.descriptor.dimension === 'residue-pair-matrix') {
        const values = layer.values as readonly MetricValue<ResiduePairIdentity>[];
        return { ...layer, values: values.filter((entry) => residueMatches(entry.identity.first) && residueMatches(entry.identity.second) && valueMatches(entry)) } as MetricLayer;
    }
    return layer;
};

export default function StructureViewerHost({
    metricLayers = EMPTY_METRIC_LAYERS,
    activeMetricId,
    showMetricWorkbench = true,
    onMetricWorkbenchVisibilityChange,
    showSequenceTrack,
    filters: controlledFilters,
    onFiltersChange,
    onMetricSelection,
    residueSelections = [],
    structureData,
    measurements: controlledMeasurements,
    onMeasurementsChange,
    showMeasurements = true,
    showComplexWorkbench = true,
    showM6Workbench = true,
    jobId,
    artifactJobId: requestedArtifactJobId,
    derivedComponents = [],
    residueMetricLayer: compatibilityLayer,
    residueColors: compatibilityColors,
    selections: callerSelections,
    onResidueClick: callerResidueClick,
    onControllerReady: callerControllerReady,
    restoredPresentation,
    ...viewerProps
}: StructureViewerHostProps) {
    const documentId = viewerProps.structureDocumentId ?? 'primary';
    const artifactJobId = requestedArtifactJobId ?? jobId;
    const [ownedStructureUrl, setOwnedStructureUrl] = useState<string | undefined>(undefined);
    useEffect(() => {
        const owner = new ViewerResourceOwner();
        const generation = owner.beginGeneration();
        if (!structureData) {
            setOwnedStructureUrl(undefined);
            return () => { void owner.dispose(); };
        }
        const url = URL.createObjectURL(new Blob([structureData], { type: 'chemical/x-pdb' }));
        owner.own(`structure-blob:${generation}`, () => URL.revokeObjectURL(url), generation);
        setOwnedStructureUrl(url);
        return () => { void owner.dispose(); };
    }, [structureData]);
    const [localFilters, setLocalFilters] = useState<StructureFilterState>(DEFAULT_FILTERS);
    const [localMeasurements, setLocalMeasurements] = useState<readonly ViewerMeasurement[]>(controlledMeasurements ?? []);
    const [selection, setSelection] = useState<MetricSelection | null>(null);
    const [selectedMetricId, setSelectedMetricId] = useState(activeMetricId);
    const [layerVisible, setLayerVisible] = useState(true);
    const [layerOpacity, setLayerOpacity] = useState(1);
    const [cameraResetToken, setCameraResetToken] = useState(0);
    const [controller, setController] = useState<StructureSceneController | null>(null);
    useEffect(() => {
        const layer = restoredPresentation?.layers?.[0];
        if (!layer) return;
        setLayerVisible(layer.visible);
        setLayerOpacity(layer.opacity);
        if (layer.metricId) setSelectedMetricId(layer.metricId);
    }, [restoredPresentation]);
    const handleControllerReady = useCallback((next: StructureSceneController | null) => {
        setController(next);
        callerControllerReady?.(next);
    }, [callerControllerReady]);
    const filters = controlledFilters ?? localFilters;
    const measurements = controlledMeasurements ?? localMeasurements;
    useEffect(() => {
        if (controlledMeasurements) setLocalMeasurements(controlledMeasurements);
    }, [controlledMeasurements]);
    useEffect(() => setSelectedMetricId(activeMetricId), [activeMetricId]);
    const setFilters = (next: StructureFilterState) => { setLocalFilters(next); onFiltersChange?.(next); };
    const setMeasurements = (next: readonly ViewerMeasurement[]) => {
        setLocalMeasurements(next);
        onMeasurementsChange?.(next);
    };
    const effectiveMetricLayers = useMemo<readonly MetricLayer[]>(() => {
        if (!compatibilityLayer) return metricLayers;
        const compatibilityMetric: MetricLayer = {
            descriptor: {
                id: compatibilityLayer.descriptor.id,
                label: compatibilityLayer.descriptor.label,
                dimension: 'residue-scalar' as const,
                units: compatibilityLayer.descriptor.units,
                direction: compatibilityLayer.descriptor.direction,
                valueRange: compatibilityLayer.descriptor.range,
                projectionPolicy: 'direct',
                normalization: 'none',
                provenance: {
                    source: compatibilityLayer.descriptor.source,
                    parameters: compatibilityLayer.descriptor.provenance,
                },
            },
            values: compatibilityLayer.points.map((point) => ({
                identity: {
                    documentId: point.residue.documentId ?? documentId,
                    labelAsymId: point.residue.labelAsymId,
                    authAsymId: point.residue.authAsymId,
                    labelSeqId: point.residue.labelSeqId,
                    authSeqId: point.residue.authSeqId,
                    insertionCode: point.residue.insertionCode ?? undefined,
                    entityId: point.residue.entityId,
                    sourceInstanceId: point.residue.instanceId,
                },
                value: point.value,
                displayColor: point.color,
            })),
        };
        return [compatibilityMetric, ...metricLayers.filter((layer) => layer.descriptor.id !== compatibilityMetric.descriptor.id)];
    }, [compatibilityLayer, documentId, metricLayers]);

    const registryState = useMemo(() => {
        const next = new MetricRegistry();
        const issues: string[] = [];
        effectiveMetricLayers.forEach((layer) => {
            const result = next.register(layer);
            if (result.status !== 'ok') issues.push(`${layer.descriptor.label}: ${result.status === 'error' ? result.error.message : result.reason}`);
        });
        return { registry: next, issues };
    }, [effectiveMetricLayers]);
    const registry = registryState.registry;
    const showLinkedSequence = showSequenceTrack ?? showMetricWorkbench;
    const visualMetricLayers = registry.list().filter((layer) => (
        layer.descriptor.dimension === 'residue-scalar'
        || layer.descriptor.dimension === 'residue-pair-matrix'
    ));
    const structureSummaryLayers = registry.list().filter((layer) => layer.descriptor.dimension === 'structure-scalar');
    const requestedLayer = selectedMetricId ? registry.get(selectedMetricId) : undefined;
    const activeLayer = requestedLayer && visualMetricLayers.some((layer) => layer.descriptor.id === requestedLayer.descriptor.id)
        ? requestedLayer
        : visualMetricLayers[0];
    const filteredLayer = activeLayer ? filterMetricLayer(activeLayer, filters) : undefined;
    const projected = filteredLayer?.descriptor.dimension === 'residue-scalar' && layerVisible
        ? projectResidueMetricLayer(filteredLayer)
        : undefined;
    const residueMetricLayer = projected?.status === 'ok'
        ? projected.value
        : (!activeLayer ? compatibilityLayer : undefined);
    const residues = useMemo(() => selectedResidues(selection), [selection]);
    const selectedResidueKeys = useMemo(() => new Set(residues.map(canonicalResidueRefKey)), [residues]);
    const linkedSelections: NonNullable<MolstarViewerProps['selections']> = residues.flatMap((residue) => {
        const chain = residue.labelAsymId;
        const number = residue.labelSeqId;
        return chain && number !== undefined ? [{ chain_id: chain, start_residue_number: number, end_residue_number: number, color: { r: 59, g: 130, b: 246 }, focus: true }] : [];
    });
    const selections = linkedSelections.length > 0 ? linkedSelections : callerSelections;
    const legacyColors = useMemo(() => compatibilityColors?.size ? adaptLegacyResidueColors(compatibilityColors) : null, [compatibilityColors]);
    const colorQueries = useMemo<readonly StructurePresentationQuery[]>(() => {
        if (!layerVisible) return [];
        const queries: StructurePresentationQuery[] = [];
        if (residueMetricLayer?.points.length) {
            queries.push(...residueMetricLayer.points.map((point) => ({
                documentId: point.residue.documentId ?? documentId, entityId: point.residue.entityId,
                labelAsymId: point.residue.labelAsymId, authAsymId: point.residue.authAsymId,
                startLabelSeqId: point.residue.labelSeqId, endLabelSeqId: point.residue.labelSeqId,
                startAuthSeqId: point.residue.authSeqId, endAuthSeqId: point.residue.authSeqId,
                insertionCode: point.residue.insertionCode, color: point.color, opacity: layerOpacity,
            })));
        } else if (legacyColors) {
            queries.push(...legacyColors.selections.map((entry) => ({
                documentId: documentId, labelAsymId: entry.struct_asym_id,
                startLabelSeqId: entry.residue_number, endLabelSeqId: entry.residue_number,
                color: entry.color, opacity: layerOpacity,
            })));
        }
        if (selections?.length) {
            queries.push(...selections.map((entry) => ({
                documentId: documentId, labelAsymId: entry.chain_id,
                startLabelSeqId: entry.start_residue_number, endLabelSeqId: entry.end_residue_number,
                color: entry.color, focus: entry.focus, opacity: layerOpacity,
            })));
        }
        if (residueSelections.length) {
            queries.push(...residueSelections.map((residue) => ({
                documentId: residue.documentId, entityId: residue.entityId,
                labelAsymId: residue.labelAsymId, authAsymId: residue.authAsymId,
                startLabelSeqId: residue.labelSeqId, endLabelSeqId: residue.labelSeqId,
                startAuthSeqId: residue.authSeqId, endAuthSeqId: residue.authSeqId,
                insertionCode: residue.insertionCode, color: { r: 16, g: 185, b: 129 }, opacity: layerOpacity,
            })));
        }
        return queries;
    }, [documentId, layerOpacity, layerVisible, legacyColors, residueMetricLayer, residueSelections, selections]);
    const tooltipQueries = useMemo((): readonly StructurePresentationQuery[] => {
        if (!layerVisible) return [];
        return residueMetricLayer?.points.map((point) => ({
            documentId: point.residue.documentId ?? documentId, entityId: point.residue.entityId, labelAsymId: point.residue.labelAsymId,
            authAsymId: point.residue.authAsymId, startLabelSeqId: point.residue.labelSeqId,
            endLabelSeqId: point.residue.labelSeqId, startAuthSeqId: point.residue.authSeqId,
            endAuthSeqId: point.residue.authSeqId, insertionCode: point.residue.insertionCode,
            tooltip: point.tooltip,
        })) ?? [];
    }, [documentId, layerVisible, residueMetricLayer]);
    const hiddenQueries = useMemo((): readonly StructurePresentationQuery[] => {
        const queries: StructurePresentationQuery[] = [];
        if (activeLayer?.descriptor.dimension === 'residue-scalar' && filteredLayer?.descriptor.dimension === 'residue-scalar') {
            const kept = new Set((filteredLayer.values as readonly MetricValue<ResidueRef>[]).map((entry) => canonicalResidueRefKey(entry.identity)));
            for (const entry of activeLayer.values as readonly MetricValue<ResidueRef>[]) {
                if (kept.has(canonicalResidueRefKey(entry.identity))) continue;
                queries.push({
                    documentId: entry.identity.documentId,
                    entityId: entry.identity.entityId,
                    labelAsymId: entry.identity.labelAsymId,
                    authAsymId: entry.identity.authAsymId,
                    startLabelSeqId: entry.identity.labelSeqId,
                    endLabelSeqId: entry.identity.labelSeqId,
                    startAuthSeqId: entry.identity.authSeqId,
                    endAuthSeqId: entry.identity.authSeqId,
                    insertionCode: entry.identity.insertionCode,
                });
            }
        }
        return queries;
    }, [activeLayer, filteredLayer]);
    const commitSelection = (next: MetricSelection) => { setSelection(next); onMetricSelection?.(next); };
    const changeMetricLayer = (metricId: string) => {
        setSelection(null);
        setSelectedMetricId(metricId);
        setLayerVisible(true);
        setLayerOpacity(1);
        setFilters(DEFAULT_FILTERS);
        setCameraResetToken((value) => value + 1);
    };
    const handleResidueClick: NonNullable<MolstarViewerProps['onResidueClick']> = (residue) => {
        const identity: ResidueRef = {
            documentId: residue.documentId,
            labelAsymId: residue.labelAsymId,
            authAsymId: residue.authAsymId,
            labelSeqId: residue.labelSeqId,
            authSeqId: residue.authSeqId,
            insertionCode: residue.insertionCode || undefined,
        };
        commitSelection({ metricId: activeLayer?.descriptor.id ?? 'structure-selection', identities: [identity], origin: 'canvas' });
        callerResidueClick?.(residue);
    };
    const scenePresentation = useMemo<StructureScenePresentation>(() => ({
        camera: restoredPresentation?.camera,
        representations: restoredPresentation?.representations,
        layers: activeLayer ? [{
            layerId: `metric:${activeLayer.descriptor.id}`,
            metricId: activeLayer.descriptor.id,
            visible: layerVisible,
            opacity: layerOpacity,
            order: 0,
        }] : [],
        selection: residues.length > 0 ? [{ selectionSetId: 'linked-selection', label: 'Linked selection', residues }] : [],
        filters,
        measurements,
        colorQueries,
        tooltipQueries,
        hiddenQueries,
        nonSelectedColor: residueMetricLayer?.nonSelectedColor ?? (legacyColors?.selections.length ? { r: 68, g: 68, b: 68 } : undefined),
    }), [activeLayer, colorQueries, filters, hiddenQueries, layerOpacity, layerVisible, legacyColors, measurements, residueMetricLayer, residues, restoredPresentation, tooltipQueries]);

    const residueLayer = filteredLayer?.descriptor.dimension === 'residue-scalar' ? filteredLayer : undefined;
    const pairLayer = filteredLayer?.descriptor.dimension === 'residue-pair-matrix'
        ? filteredLayer
        : undefined;
    const residueValues = residueLayer?.descriptor.dimension === 'residue-scalar'
        ? residueLayer.values as readonly MetricValue<ResidueRef>[] : [];
    const activeResidues: readonly ResidueRef[] = activeLayer?.descriptor.dimension === 'residue-scalar'
        ? (activeLayer.values as readonly MetricValue<ResidueRef>[]).map((entry) => entry.identity)
        : activeLayer?.descriptor.dimension === 'residue-pair-matrix'
            ? (activeLayer.values as readonly MetricValue<ResiduePairIdentity>[]).flatMap((entry) => [entry.identity.first, entry.identity.second])
            : [];
    const chains = [...new Set(activeResidues.map((residue) => residue.labelAsymId ?? residue.authAsymId).filter((value): value is string => Boolean(value)))].sort();
    const chainPairLayers = registry.list().filter((layer) => layer.descriptor.dimension === 'chain-pair-scalar') as readonly Extract<MetricLayer, { descriptor: { dimension: 'chain-pair-scalar' } }>[];
    const geometryLayers = registry.list().filter((layer) => layer.descriptor.dimension === 'geometry-annotation') as readonly Extract<MetricLayer, { descriptor: { dimension: 'geometry-annotation' } }>[];
    const hasComplexAnalysis = derivedComponents.length > 0 || chainPairLayers.length > 0 || geometryLayers.length > 0;
    const exportRows = useMemo(() => registry.list().flatMap((layer) => layer.values.map((entry) => ({
        metric_id: layer.descriptor.id,
        metric_label: layer.descriptor.label,
        units: layer.descriptor.units ?? null,
        identity: exportMetricIdentity(entry.identity),
        value: entry.missingness === undefined ? entry.value : null,
        missingness: entry.missingness ?? null,
    }))), [registry]);
    const hasWorkbenchContent = Boolean(showM6Workbench || activeLayer || structureSummaryLayers.length > 0 || showMeasurements || (showComplexWorkbench && hasComplexAnalysis));

    const hostHeight = typeof viewerProps.height === 'number' ? `${viewerProps.height}px` : viewerProps.height;

    return (
        <div
            className={`relative w-full ${hostHeight ? '' : 'h-full'}`}
            style={hostHeight ? { height: hostHeight } : undefined}
            data-bms-structure-viewer-host="direct-4.5.0"
        >
            <MolstarViewerImpl {...viewerProps} artifactJobId={artifactJobId} structureUrl={ownedStructureUrl ?? viewerProps.structureUrl} selections={selections} residueMetricLayer={residueMetricLayer} measurements={measurements} scenePresentation={scenePresentation} cameraResetToken={cameraResetToken} onResidueClick={handleResidueClick} onControllerReady={handleControllerReady} />
            {!showMetricWorkbench && hasWorkbenchContent && onMetricWorkbenchVisibilityChange && (
                <button
                    type="button"
                    onClick={() => onMetricWorkbenchVisibilityChange(true)}
                    className="absolute right-2 top-2 z-40 rounded border border-blue-500/50 bg-slate-950/90 px-3 py-1.5 text-xs font-semibold text-blue-200 shadow-lg hover:bg-slate-800"
                >
                    Show metrics
                </button>
            )}
            {(showMetricWorkbench || showLinkedSequence || showM6Workbench) && hasWorkbenchContent && (
                <aside className="absolute bottom-2 right-2 z-30 max-h-[55%] w-[min(28rem,calc(100%-1rem))] space-y-2 overflow-auto rounded bg-slate-950/90 p-2 shadow-xl" aria-label={showMetricWorkbench ? 'Structure metric workbench' : showM6Workbench ? 'Structure reproducibility workbench' : 'Linked sequence overlay'}>
                    {showMetricWorkbench && (
                        <div className="flex items-center justify-between border-b border-slate-700/70 pb-2 text-xs font-semibold text-slate-200">
                            <span>Metrics</span>
                            {onMetricWorkbenchVisibilityChange && (
                                <button
                                    type="button"
                                    aria-label="Minimize metric workbench"
                                    title="Minimize metric workbench"
                                    onClick={() => onMetricWorkbenchVisibilityChange(false)}
                                    className="rounded border border-slate-600 px-2 py-0.5 text-slate-300 hover:bg-slate-800 hover:text-white"
                                >
                                    —
                                </button>
                            )}
                        </div>
                    )}
                    {showMetricWorkbench && activeLayer && <label className="block text-xs text-slate-300">Visual layer
                        <select className="mt-1 w-full rounded bg-slate-800 p-1" value={activeLayer.descriptor.id} onChange={(event) => changeMetricLayer(event.target.value)}>
                            {visualMetricLayers.map((layer) => <option key={layer.descriptor.id} value={layer.descriptor.id}>{layer.descriptor.label}</option>)}
                        </select>
                    </label>}
                    {showMetricWorkbench && structureSummaryLayers.length > 0 && (
                        <details className="rounded border border-slate-700 bg-slate-900/95 p-2 text-xs text-slate-200">
                            <summary className="cursor-pointer font-semibold">Structure summary ({structureSummaryLayers.length})</summary>
                            <div className="mt-1 text-[10px] text-slate-400">Non-spatial values; these do not color or focus the 3D structure.</div>
                            <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1">
                                {structureSummaryLayers.map((layer) => {
                                    const value = layer.values[0]?.value;
                                    return <div key={layer.descriptor.id} className="contents"><dt className="text-slate-400">{layer.descriptor.label}</dt><dd className="font-mono text-slate-100">{typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(value ?? '—')}{layer.descriptor.units ? ` ${layer.descriptor.units}` : ''}</dd></div>;
                                })}
                            </dl>
                        </details>
                    )}
                    {showMetricWorkbench && activeLayer && <MetricLegendPanel layer={activeLayer} visible={layerVisible} opacity={layerOpacity} onVisibilityChange={setLayerVisible} onOpacityChange={setLayerOpacity} onReset={() => { setLayerVisible(true); setLayerOpacity(1); setFilters(DEFAULT_FILTERS); }} />}
                    {showMetricWorkbench && <FilterPanel value={filters} availableChains={chains} metricRange={activeLayer?.descriptor.valueRange} onChange={setFilters} />}
                    {showLinkedSequence && residueLayer && <SequenceTrackExtension metricId={residueLayer.descriptor.id} points={residueValues.map((entry) => ({ residue: entry.identity, label: residueLabel(entry.identity), value: typeof entry.value === 'number' ? entry.value : null }))} selectedKeys={selectedResidueKeys} onSelection={commitSelection} />}
                    {showMetricWorkbench && pairLayer && <PairMatrixExtension layer={pairLayer} onSelection={commitSelection} />}
                    {showMetricWorkbench && showComplexWorkbench && <ComplexAnalysisPanel components={derivedComponents} chainPairLayers={chainPairLayers} geometryLayers={geometryLayers} onSelection={commitSelection} />}
                    {showMetricWorkbench && showMeasurements && <MeasurementPanel documentId={documentId} measurements={measurements} onChange={setMeasurements} />}
                    {showM6Workbench && <M6WorkbenchPanel controller={controller} jobId={jobId} tableRows={exportRows} />}
                    {showMetricWorkbench && (registryState.issues.length > 0 || (projected && projected.status !== 'ok')) && <div role="alert" className="rounded bg-red-950/80 p-2 text-xs text-red-200">{[...registryState.issues, ...(projected && projected.status !== 'ok' ? [projected.status === 'error' ? projected.error.message : projected.reason] : [])].join(' · ')}</div>}
                </aside>
            )}
        </div>
    );
}
