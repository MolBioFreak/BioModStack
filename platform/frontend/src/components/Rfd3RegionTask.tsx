import { Rfd3PartialNoiseInput } from './Rfd3PartialNoiseInput';

export const RFD3_REGION_TASKS = [
    ['', 'Generate from the structure as context'],
    ['partial_diffusion', 'Partial diffusion: redesign chosen regions'],
    ['minimal_insertion', 'Minimal insertion: add residues after an anchor'],
    ['native_recall', 'Native recall: native contig and selectors'],
] as const;

type Settings = Record<string, unknown>;
const obj = (value: unknown): Settings => value && typeof value === 'object' && !Array.isArray(value) ? value as Settings : {};
const text = (value: unknown) => typeof value === 'string' ? value : '';

/** Region-level RFD3 tasks. The API lowers these to native conditioning; the browser only collects typed fields. */
export function Rfd3RegionTask({ settings, onChange }: { settings: Settings; onChange: (next: (previous: Settings) => Settings) => void }) {
    const task = text(settings.generation_task);
    const region = obj(settings.redesign);
    const conditioning = obj(settings.conditioning);
    const setTask = (value: string) => onChange(previous => {
        const next = { ...previous };
        if (!value) { delete next.generation_task; delete next.redesign; return next; }
        next.generation_task = value;
        const kept = obj(previous.conditioning);
        if (value !== 'partial_diffusion') delete kept.partial_t;
        next.conditioning = { ...kept };
        if (value === 'native_recall') delete next.redesign;
        else next.redesign = { design_chains: ['A'], ...obj(previous.redesign) };
        return next;
    });
    const setRegion = (patch: Settings) => onChange(previous => {
        const merged: Settings = { ...obj(previous.redesign), ...patch };
        for (const key of Object.keys(merged)) if (merged[key] === '' || merged[key] === undefined) delete merged[key];
        return { ...previous, redesign: merged };
    });
    const setPartial = (value: number | '' | null) => onChange(previous => {
        const next = { ...obj(previous.conditioning) };
        if (value === '' || value === null) delete next.partial_t; else next.partial_t = value;
        return { ...previous, conditioning: next };
    });
    const chains = Array.isArray(region.design_chains) ? (region.design_chains as unknown[]).map(String).join(',') : '';
    const partialT = typeof conditioning.partial_t === 'number' ? conditioning.partial_t : '';
    return <fieldset className="space-y-3"><legend>Redesign task</legend>
        <p>Redesign regions of the input structure. Native RFD3 takes the size from the structure, so the length range is not sent for these tasks.</p>
        <label>Task<select aria-label="RFD3 region task" value={task} onChange={event => setTask(event.target.value)}>
            {RFD3_REGION_TASKS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        {task && task !== 'native_recall' && <label>Design chain(s)<input aria-label="Redesign design chains" value={chains}
            onChange={event => setRegion({ design_chains: event.target.value.split(/[,\s]+/).filter(Boolean) })} /></label>}
        {task === 'partial_diffusion' && <>
            <label>Residues to redesign<input aria-label="Redesign ranges" placeholder="A30-40" value={text(region.redesign_ranges)}
                onChange={event => setRegion({ redesign_ranges: event.target.value })} /></label>
            <Rfd3PartialNoiseInput value={partialT} onChange={setPartial} />
        </>}
        {task === 'minimal_insertion' && <>
            <label>Insert after residue<input aria-label="Insertion anchor" placeholder="A40" value={text(region.insertion_anchor)}
                onChange={event => setRegion({ insertion_anchor: event.target.value })} /></label>
            <label>Minimum inserted residues<input aria-label="Insertion minimum length" type="number" min={1} value={region.insertion_min_length as number | undefined ?? ''}
                onChange={event => setRegion({ insertion_min_length: event.target.value === '' ? '' : Number(event.target.value) })} /></label>
            <label>Maximum inserted residues<input aria-label="Insertion maximum length" type="number" min={1} value={region.insertion_max_length as number | undefined ?? ''}
                onChange={event => setRegion({ insertion_max_length: event.target.value === '' ? '' : Number(event.target.value) })} /></label>
        </>}
        {task === 'native_recall' && <p>Set the native contig and selectors under Conditions.</p>}
    </fieldset>;
}
