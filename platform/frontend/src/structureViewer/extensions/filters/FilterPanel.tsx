import type { StructureFilterState } from '../../contracts/scenePresentation.js';

export interface FilterPanelProps {
    readonly value: StructureFilterState;
    readonly availableChains: readonly string[];
    readonly metricRange?: readonly [number, number];
    readonly onChange: (value: StructureFilterState) => void;
}

const ENTITY_TYPES = ['protein', 'dna', 'rna', 'ligand', 'glycan', 'ion', 'water', 'unknown'] as const;

export function FilterPanel({ value, availableChains, metricRange, onChange }: FilterPanelProps) {
    const selected = new Set(value.chainIds ?? []);
    const entities = new Set(value.entityTypes ?? ENTITY_TYPES);
    const toggleChain = (chainId: string) => onChange({
        ...value,
        chainIds: selected.has(chainId) ? [...selected].filter((id) => id !== chainId) : [...selected, chainId],
    });
    const toggleEntity = (entity: typeof ENTITY_TYPES[number]) => onChange({
        ...value,
        entityTypes: entities.has(entity) ? [...entities].filter((id) => id !== entity) : [...entities, entity],
    });
    return (
        <fieldset className="rounded border border-slate-700 p-3 text-xs" aria-label="Structure filters">
            <legend className="px-1 font-semibold">Filters</legend>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Entity types">
                {ENTITY_TYPES.map((entity) => <label key={entity} className="flex items-center gap-1"><input type="checkbox" checked={entities.has(entity)} onChange={() => toggleEntity(entity)} /> {entity}</label>)}
            </div>
            <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Chains">
                {availableChains.map((chainId) => (
                    <label key={chainId} className="flex items-center gap-1"><input type="checkbox" checked={selected.has(chainId)} onChange={() => toggleChain(chainId)} /> Chain {chainId}</label>
                ))}
            </div>
            {availableChains.length > 0 && <div className="mt-1 text-[10px] text-slate-400">No checked chain means all chains.</div>}
            <div className="mt-2 grid grid-cols-2 gap-2">
                <label>Residue min<input className="w-full" type="number" value={value.residueRange?.[0] ?? ''} onChange={(event) => onChange({ ...value, residueRange: [Number(event.target.value), value.residueRange?.[1] ?? Number.MAX_SAFE_INTEGER] })} /></label>
                <label>Residue max<input className="w-full" type="number" value={value.residueRange?.[1] ?? ''} onChange={(event) => onChange({ ...value, residueRange: [value.residueRange?.[0] ?? Number.MIN_SAFE_INTEGER, Number(event.target.value)] })} /></label>
            </div>
            {metricRange && (
                <div className="mt-2 grid grid-cols-2 gap-2">
                    <label>Metric min<input className="w-full" type="number" value={value.metricRange?.[0] ?? metricRange[0]} onChange={(event) => onChange({ ...value, metricRange: [Number(event.target.value), value.metricRange?.[1] ?? metricRange[1]] })} /></label>
                    <label>Metric max<input className="w-full" type="number" value={value.metricRange?.[1] ?? metricRange[1]} onChange={(event) => onChange({ ...value, metricRange: [value.metricRange?.[0] ?? metricRange[0], Number(event.target.value)] })} /></label>
                </div>
            )}
            <label className="mt-2 flex items-center gap-1"><input type="checkbox" checked={value.includeMissing ?? false} onChange={(event) => onChange({ ...value, includeMissing: event.target.checked })} /> Include missing values</label>
        </fieldset>
    );
}
