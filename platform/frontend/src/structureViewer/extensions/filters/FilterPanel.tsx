import { useEffect, useRef, useState } from 'react';
import type { StructureFilterState } from '../../contracts/scenePresentation.js';

export interface FilterPanelProps {
    readonly value: StructureFilterState;
    readonly availableChains: readonly string[];
    readonly metricRange?: readonly [number, number];
    readonly metricUnits?: string;
    readonly metricDisplayScale?: number;
    readonly onChange: (value: StructureFilterState) => void;
}

const ENTITY_TYPES = ['protein', 'dna', 'rna', 'ligand', 'glycan', 'ion', 'water', 'unknown'] as const;

function MetricBoundInput({ value, scale, onChange }: { value: number | null | undefined; scale: number; onChange: (text: string) => void }) {
    const [draft, setDraft] = useState(value == null ? '' : String(value * scale));
    const emitted = useRef({ value: value ?? undefined, scale });
    useEffect(() => {
        if (!Object.is(emitted.current.value, value ?? undefined) || emitted.current.scale !== scale) {
            setDraft(value == null ? '' : String(value * scale));
            emitted.current = { value: value ?? undefined, scale };
        }
    }, [value, scale]);
    return <input className="min-w-0 w-full" type="number" step="any" value={draft} placeholder="Unbounded" onChange={event => {
        const text = event.target.value;
        setDraft(text);
        const parsed = text.trim() === '' ? undefined : Number(text) / scale;
        if (parsed === undefined || Number.isFinite(parsed)) emitted.current = { value: parsed, scale };
        onChange(text);
    }} />;
}

export function FilterPanel({ value, availableChains, metricRange, metricUnits, metricDisplayScale = 1, onChange }: FilterPanelProps) {
    const setBound = (field: 'residueRange' | 'metricRange', index: 0 | 1, text: string) => {
        const parsed = text.trim() === '' ? undefined : Number(text) / (field === 'metricRange' ? metricDisplayScale : 1);
        if (parsed !== undefined && !Number.isFinite(parsed)) return;
        const bounds = [...(value[field] ?? [undefined, undefined])] as [number | null | undefined, number | null | undefined];
        bounds[index] = parsed;
        onChange({ ...value, [field]: bounds.every(bound => bound == null) ? undefined : bounds });
    };
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
                <label>Residue min<input className="min-w-0 w-full" type="number" step="any" value={value.residueRange?.[0] ?? ''} onChange={(event) => setBound('residueRange', 0, event.target.value)} /></label>
                <label>Residue max<input className="min-w-0 w-full" type="number" step="any" value={value.residueRange?.[1] ?? ''} onChange={(event) => setBound('residueRange', 1, event.target.value)} /></label>
            </div>
            {metricRange && <p className="mt-2 text-slate-400">{metricDisplayScale === 100 ? 'pLDDT display / 100; native fraction = display ÷ 100.' : `Native metric units: ${metricUnits || 'unspecified'}.`} Empty means unbounded; stored values and exports are unchanged.</p>}
            {metricRange && (
                <div className="mt-2 grid grid-cols-2 gap-2">
                    <label>Metric min{metricDisplayScale === 100 || metricUnits === 'percent' ? ' / 100' : ''}<MetricBoundInput value={value.metricRange?.[0]} scale={metricDisplayScale} onChange={text => setBound('metricRange', 0, text)} /></label>
                    <label>Metric max{metricDisplayScale === 100 || metricUnits === 'percent' ? ' / 100' : ''}<MetricBoundInput value={value.metricRange?.[1]} scale={metricDisplayScale} onChange={text => setBound('metricRange', 1, text)} /></label>
                </div>
            )}
            <label className="mt-2 flex items-center gap-1"><input type="checkbox" checked={value.includeMissing ?? false} onChange={(event) => onChange({ ...value, includeMissing: event.target.checked })} /> Include missing values</label>
        </fieldset>
    );
}
