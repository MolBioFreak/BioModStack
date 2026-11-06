import { useEffect, useRef, useState } from 'react';
import { api, type NativeGenerationSourceBindings, type StructureMaterialization } from './api';
import type { Rfd4ConditionInspection } from '../components/Rfd4TaskWorkspace';
import type { Rfd4NativeSelectionIdentity } from './rfd4NativeSelection';
import type { MetricLayer } from '../structureViewer/metrics/metricContracts';

type Readback = {
    row: Rfd4ConditionInspection; example_name: string; scope: string; runtime_image: string;
    input_as_annotated_cif: boolean; unresolved_atoms: unknown[];
    sources: Record<string, { sha256: string; model_number: number | null; model_numbers: number[]; identities: Rfd4NativeSelectionIdentity[] }>;
};

/** Native row targets are an observation layer, never temporary authoring picks. */
export function useRfd4ConditionInspection(input: unknown, sources: NativeGenerationSourceBindings | undefined, documentGeneration: number, task: string) {
    const identity = JSON.stringify([input, sources, documentGeneration, task]);
    const epoch = useRef({ identity, generation: 0 });
    if (epoch.current.identity !== identity) epoch.current = { identity, generation: epoch.current.generation + 1 };
    const serial = useRef(0);
    const [state, setState] = useState<{ generation: number; status: string; result?: Readback }>();
    useEffect(() => () => { serial.current++; }, []);
    const inspect = async (row: Rfd4ConditionInspection) => {
        const generation = epoch.current.generation, call = ++serial.current;
        setState({ generation, status: 'Inspecting current condition with the native parser…' });
        try {
            const { data } = await api.post<Readback>('/api/models/rfd4_proteina/condition-inspection', { input, native_sources: sources ?? {}, row });
            if (call !== serial.current || generation !== epoch.current.generation) return;
            const count = Object.values(data.sources).reduce((n, source) => n + source.identities.length, 0);
            setState({ generation, result: data, status: `${data.example_name} · ${row.family} ${row.value} row ${row.rowIndex + 1}: ${count} exact source atoms; ${data.unresolved_atoms.length} unmapped native atoms. ${data.input_as_annotated_cif ? 'Preserve annotated CIF' : 'Fresh native parsing'}. ${data.scope}. Inspection does not change temporary selection or the request.` });
        } catch (cause) {
            if (call !== serial.current || generation !== epoch.current.generation) return;
            setState({ generation, status: `Condition inspection unavailable: ${cause instanceof Error ? cause.message : String(cause)}. Manual conditions and Generate remain available.` });
        }
    };
    const current = state?.generation === epoch.current.generation ? state : undefined;
    const layers = (slot: string, receipt?: StructureMaterialization): MetricLayer[] => {
        const result = current?.result, source = result?.sources[slot];
        if (!result || !source || !receipt || !receipt.sha256 || source.sha256 !== receipt.sha256 ||
            source.model_number !== (receipt.model_number ?? null) || JSON.stringify(source.model_numbers) !== JSON.stringify(receipt.model_numbers)) return [];
        return [{ descriptor: { id: `current-condition:${slot}`, label: `Inspected row · ${result.row.family} ${result.row.value}`,
            dimension: 'atom-scalar', units: null, direction: 'neutral', projectionPolicy: 'direct', normalization: 'none',
            description: result.scope, provenance: { source: 'Selected native RFD4 condition parser', sourceVersion: result.runtime_image },
            palette: { colors: ['#06b6d4', '#06b6d4'], domain: [0, 1] } },
            values: source.identities.map(identity => ({ identity: { ...identity, documentId: receipt.path }, value: 1 })) }];
    };
    return { inspect, layers, status: current?.status ?? (state ? 'Condition inspection is stale after a source, document or task edit. Inspect the current row again.' : '') };
}
