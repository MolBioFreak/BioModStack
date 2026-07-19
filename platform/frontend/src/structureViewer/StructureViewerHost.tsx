import { useEffect, useMemo, useRef, useState } from 'react';

import MolstarViewerImpl from '../components/MolstarViewerImpl';
import type { MolstarViewerProps } from '../components/MolstarViewerImpl';
import { adaptLegacyResidueColors } from './adapters/residueColorSelections';
import type { StructureFilterState, StructurePresentationQuery, StructureScenePresentation } from './contracts/scenePresentation.js';
import { canonicalResidueRefKey, type ResidueRef } from './contracts/structureIdentity.js';
import { FilterPanel } from './extensions/filters/FilterPanel';
import { MetricLegendPanel } from './extensions/metrics/MetricLegendPanel';
import { PairMatrixExtension } from './extensions/pairMatrix/PairMatrixExtension';
import { ViewerResourceOwner } from './runtime/resourceOwnership.js';
import { SequenceTrackExtension } from './extensions/sequence/SequenceTrackExtension';
import type { MetricLayer, MetricSelection, MetricValue, ResiduePairIdentity } from './metrics/metricContracts.js';
import { MetricRegistry } from './metrics/MetricRegistry.js';
import { projectResidueMetricLayer } from './metrics/metricProjection.js';

export interface StructureViewerHostProps extends MolstarViewerProps {
    readonly metricLayers?: readonly MetricLayer[];
    readonly activeMetricId?: string;
    readonly showMetricWorkbench?: boolean;
    readonly filters?: StructureFilterState;
    readonly onFiltersChange?: (filters: StructureFilterState) => void;
    readonly onMetricSelection?: (selection: MetricSelection) => void;
    readonly residueSelections?: readonly ResidueRef[];
    readonly structureData?: string;
}

const EMPTY_METRIC_LAYERS: readonly MetricLayer[] = [];
const DEFAULT_FILTERS: StructureFilterState = { includeMissing: false };

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
    filters: controlledFilters,
    onFiltersChange,
    onMetricSelection,
    residueSelections = [],
    structureData,
    residueMetricLayer: compatibilityLayer,
    residueColors: compatibilityColors,
    selections: callerSelections,
    onResidueClick: callerResidueClick,
    ...viewerProps
}: StructureViewerHostProps) {
    const resourceOwnerRef = useRef<ViewerResourceOwner | null>(null);
    if (!resourceOwnerRef.current) resourceOwnerRef.current = new ViewerResourceOwner();
    const [ownedStructureUrl, setOwnedStructureUrl] = useState<string | undefined>(undefined);
    useEffect(() => {
        const owner = resourceOwnerRef.current!;
        const generation = owner.beginGeneration();
        if (!structureData) { setOwnedStructureUrl(undefined); return undefined; }
        const url = URL.createObjectURL(new Blob([structureData], { type: 'chemical/x-pdb' }));
        owner.own(`structure-blob:${generation}`, () => URL.revokeObjectURL(url), generation);
        setOwnedStructureUrl(url);
        return () => { void owner.disposeGeneration(generation); };
    }, [structureData]);
    useEffect(() => () => { void resourceOwnerRef.current?.dispose(); }, []);
    const [localFilters, setLocalFilters] = useState<StructureFilterState>({ includeMissing: false });
    const [selection, setSelection] = useState<MetricSelection | null>(null);
    const [selectedMetricId, setSelectedMetricId] = useState(activeMetricId);
    const [layerVisible, setLayerVisible] = useState(true);
    const [layerOpacity, setLayerOpacity] = useState(1);
    const filters = controlledFilters ?? localFilters;
    useEffect(() => setSelectedMetricId(activeMetricId), [activeMetricId]);
    const setFilters = (next: StructureFilterState) => { setLocalFilters(next); onFiltersChange?.(next); };
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
                    documentId: point.residue.documentId ?? 'primary',
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
    }, [compatibilityLayer, metricLayers]);

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
    const activeLayer = (selectedMetricId ? registry.get(selectedMetricId) : undefined) ?? registry.list()[0];
    const filteredLayer = activeLayer ? filterMetricLayer(activeLayer, filters) : undefined;
    const projected = filteredLayer && layerVisible ? projectResidueMetricLayer(filteredLayer) : undefined;
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
                documentId: point.residue.documentId ?? 'primary', entityId: point.residue.entityId,
                labelAsymId: point.residue.labelAsymId, authAsymId: point.residue.authAsymId,
                startLabelSeqId: point.residue.labelSeqId, endLabelSeqId: point.residue.labelSeqId,
                startAuthSeqId: point.residue.authSeqId, endAuthSeqId: point.residue.authSeqId,
                insertionCode: point.residue.insertionCode, color: point.color, opacity: layerOpacity,
            })));
        } else if (legacyColors) {
            queries.push(...legacyColors.selections.map((entry) => ({
                documentId: 'primary', labelAsymId: entry.struct_asym_id,
                startLabelSeqId: entry.residue_number, endLabelSeqId: entry.residue_number,
                color: entry.color, opacity: layerOpacity,
            })));
        }
        if (selections?.length) {
            queries.push(...selections.map((entry) => ({
                documentId: 'primary', labelAsymId: entry.chain_id,
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
    }, [layerOpacity, layerVisible, legacyColors, residueMetricLayer, residueSelections, selections]);
    const tooltipQueries = useMemo((): readonly StructurePresentationQuery[] => {
        if (!layerVisible) return [];
        return residueMetricLayer?.points.map((point) => ({
            documentId: point.residue.documentId ?? 'primary', entityId: point.residue.entityId, labelAsymId: point.residue.labelAsymId,
            authAsymId: point.residue.authAsymId, startLabelSeqId: point.residue.labelSeqId,
            endLabelSeqId: point.residue.labelSeqId, startAuthSeqId: point.residue.authSeqId,
            endAuthSeqId: point.residue.authSeqId, insertionCode: point.residue.insertionCode,
            tooltip: point.tooltip,
        })) ?? [];
    }, [layerVisible, residueMetricLayer]);
    const commitSelection = (next: MetricSelection) => { setSelection(next); onMetricSelection?.(next); };
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
        layers: activeLayer ? [{
            layerId: `metric:${activeLayer.descriptor.id}`,
            metricId: activeLayer.descriptor.id,
            visible: layerVisible,
            opacity: layerOpacity,
            order: 0,
        }] : [],
        selection: residues.length > 0 ? [{ selectionSetId: 'linked-selection', label: 'Linked selection', residues }] : [],
        filters,
        measurements: viewerProps.measurements,
        colorQueries,
        tooltipQueries,
        nonSelectedColor: residueMetricLayer?.nonSelectedColor ?? (legacyColors?.selections.length ? { r: 68, g: 68, b: 68 } : undefined),
    }), [activeLayer, colorQueries, filters, layerOpacity, layerVisible, legacyColors, residueMetricLayer, residues, tooltipQueries, viewerProps.measurements]);

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

    return (
        <div className="relative h-full w-full" data-bms-structure-viewer-host="direct-4.5.0">
            <MolstarViewerImpl {...viewerProps} structureUrl={ownedStructureUrl ?? viewerProps.structureUrl} selections={selections} residueMetricLayer={residueMetricLayer} scenePresentation={scenePresentation} onResidueClick={handleResidueClick} />
            {showMetricWorkbench && activeLayer && (
                <aside className="absolute bottom-2 right-2 z-30 max-h-[55%] w-[min(28rem,calc(100%-1rem))] space-y-2 overflow-auto rounded bg-slate-950/90 p-2 shadow-xl" aria-label="Structure metric workbench">
                    <label className="block text-xs text-slate-300">Metric layer
                        <select className="mt-1 w-full rounded bg-slate-800 p-1" value={activeLayer.descriptor.id} onChange={(event) => setSelectedMetricId(event.target.value)}>
                            {registry.list().map((layer) => <option key={layer.descriptor.id} value={layer.descriptor.id}>{layer.descriptor.label}</option>)}
                        </select>
                    </label>
                    <MetricLegendPanel layer={activeLayer} visible={layerVisible} opacity={layerOpacity} onVisibilityChange={setLayerVisible} onOpacityChange={setLayerOpacity} onReset={() => { setLayerVisible(true); setLayerOpacity(1); setFilters(DEFAULT_FILTERS); }} />
                    <FilterPanel value={filters} availableChains={chains} metricRange={activeLayer.descriptor.valueRange} onChange={setFilters} />
                    {residueLayer && <SequenceTrackExtension metricId={residueLayer.descriptor.id} points={residueValues.map((entry) => ({ residue: entry.identity, label: residueLabel(entry.identity), value: typeof entry.value === 'number' ? entry.value : null }))} selectedKeys={selectedResidueKeys} onSelection={commitSelection} />}
                    {pairLayer && <PairMatrixExtension layer={pairLayer} onSelection={commitSelection} />}
                    {(registryState.issues.length > 0 || (projected && projected.status !== 'ok')) && <div role="alert" className="rounded bg-red-950/80 p-2 text-xs text-red-200">{[...registryState.issues, ...(projected && projected.status !== 'ok' ? [projected.status === 'error' ? projected.error.message : projected.reason] : [])].join(' · ')}</div>}
                </aside>
            )}
        </div>
    );
}
