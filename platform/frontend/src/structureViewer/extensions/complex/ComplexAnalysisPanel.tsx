import type { AtomRef, ResidueRef } from '../../contracts/structureIdentity.js';
import type { DerivedStructureComponent } from '../../contracts/complexAnalysis.js';
import type {
    ChainPairIdentity,
    GeometryAnnotationIdentity,
    MetricLayer,
    MetricSelection,
    MetricValue,
} from '../../metrics/metricContracts.js';

export interface ComplexAnalysisPanelProps {
    readonly components: readonly DerivedStructureComponent[];
    readonly chainPairLayers: readonly Extract<MetricLayer, { descriptor: { dimension: 'chain-pair-scalar' } }>[];
    readonly geometryLayers: readonly Extract<MetricLayer, { descriptor: { dimension: 'geometry-annotation' } }>[];
    readonly onSelection: (selection: MetricSelection) => void;
}

const metricText = (value: MetricValue<unknown>, units: string | null): string => {
    if (value.missingness) return value.missingness.replaceAll('_', ' ');
    if (typeof value.value === 'number') {
        return `${value.value.toLocaleString(undefined, { maximumFractionDigits: 4 })}${units ? ` ${units}` : ''}`;
    }
    return String(value.value ?? '—');
};

const pairLabel = (identity: ChainPairIdentity): string => (
    `${identity.firstChainId}${identity.firstInstanceId ? ` [${identity.firstInstanceId}]` : ''}`
    + ` ↔ ${identity.secondChainId}${identity.secondInstanceId ? ` [${identity.secondInstanceId}]` : ''}`
);

const annotationSelection = (identity: GeometryAnnotationIdentity): readonly (ResidueRef | AtomRef)[] => (
    identity.atoms?.length ? identity.atoms : identity.residues ?? []
);

export function ComplexAnalysisPanel({ components, chainPairLayers, geometryLayers, onSelection }: ComplexAnalysisPanelProps) {
    if (components.length === 0 && chainPairLayers.length === 0 && geometryLayers.length === 0) return null;
    return (
        <details className="rounded border border-cyan-700/70 bg-slate-900/95 p-2 text-xs text-slate-200">
            <summary className="cursor-pointer font-semibold">Complexes, interfaces & interactions</summary>
            <p className="mt-1 text-[10px] text-slate-400">Persisted producer values and explicitly labeled BioModStack-derived records only. Missing analysis is not interpreted as absence.</p>
            {components.length > 0 && <section className="mt-2">
                <h4 className="font-semibold text-slate-300">Derived chain inventory</h4>
                <p className="text-[10px] text-amber-300">Structure-derived chain classes; not assembly/operator component-instance identity.</p>
                <ul className="mt-1 space-y-1">
                    {components.map((component) => <li key={`${component.documentId}:${component.chainId}`} className="rounded bg-slate-800 px-2 py-1">
                        <span className="font-mono">{component.chainId}</span> · {component.componentType} · {component.length.toLocaleString()} positions
                        <div className="text-[10px] text-slate-500">{component.provenance}</div>
                    </li>)}
                </ul>
            </section>}
            {chainPairLayers.map((layer) => (
                <section key={layer.descriptor.id} className="mt-2">
                    <div className="font-semibold">{layer.descriptor.label}</div>
                    <div className="text-[10px] text-slate-400">{layer.descriptor.description ?? layer.descriptor.semantics}</div>
                    <ul className="mt-1 space-y-1">
                        {(layer.values as readonly MetricValue<ChainPairIdentity>[]).map((entry) => (
                            <li key={`${entry.identity.documentId}:${pairLabel(entry.identity)}`} className="flex justify-between gap-3 rounded bg-slate-950/70 px-2 py-1">
                                <span>{pairLabel(entry.identity)}</span>
                                <span className="font-mono">{metricText(entry, layer.descriptor.units)}</span>
                            </li>
                        ))}
                    </ul>
                </section>
            ))}
            {geometryLayers.map((layer) => (
                <section key={layer.descriptor.id} className="mt-2">
                    <div className="font-semibold">{layer.descriptor.label}</div>
                    <ul className="mt-1 space-y-1">
                        {(layer.values as readonly MetricValue<GeometryAnnotationIdentity>[]).map((entry) => {
                            const identities = annotationSelection(entry.identity);
                            return (
                                <li key={entry.identity.annotationId}>
                                    <button
                                        type="button"
                                        disabled={identities.length === 0}
                                        onClick={() => onSelection({
                                            metricId: layer.descriptor.id,
                                            identities,
                                            origin: 'table',
                                        })}
                                        className="flex w-full justify-between gap-3 rounded bg-slate-950/70 px-2 py-1 text-left enabled:hover:bg-cyan-950/60 disabled:cursor-not-allowed disabled:opacity-60"
                                    >
                                        <span>{entry.identity.annotationId}</span>
                                        <span className="font-mono">{metricText(entry, layer.descriptor.units)}</span>
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                </section>
            ))}
        </details>
    );
}
