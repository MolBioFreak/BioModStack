import type { ProtenixComponent } from './protenixChemistry';
import { protenixEntityKey } from './protenixChemistry';

export interface Esmfold2ComponentSettings {
    modifications?: Array<{ position: number; ccd: string }>;
    msa?: null | { sequences: string[] };
    msa_path?: string;
    source_sequence?: string;
}
export interface Esmfold2Bond {
    chain_id1: string; res_idx1: number; atom_idx1: number;
    chain_id2: string; res_idx2: number; atom_idx2: number;
}
export interface Esmfold2Distogram { chain_id: string; distogram: number[][] }

// The roster is shared, but same-spelled numeric annotations are not.
// In particular Boltz position=1 must never become ESM position=1 implicitly.
export function esmfold2ComponentSettings(component: ProtenixComponent): Esmfold2ComponentSettings {
    const native = component as unknown as Esmfold2ComponentSettings;
    return { modifications: native.modifications, msa: native.msa, msa_path: native.msa_path,
        source_sequence: component.sequence };
}
export function withoutEsmfold2Annotations(component: ProtenixComponent): ProtenixComponent {
    const result = { ...component };
    delete result.modifications; delete result.msa; delete result.msa_path;
    return result;
}
export function projectEsmfold2Component(component: ProtenixComponent, settings: Esmfold2ComponentSettings = {}) {
    const { id, type, sequence, name, count, copy_ids, smiles, ccd, ligand_file, ion, element } = component;
    const { source_sequence, ...native } = settings;
    if (source_sequence !== undefined && source_sequence !== sequence) {
        if (native.msa !== null) delete native.msa;
        delete native.msa_path;
    }
    return { id, type, sequence, name, count, copy_ids, smiles, ccd, ligand_file, ion, element, ...native };
}


export function hydrateEsmfold2Document(values?: Record<string, UntypedApiValue>) {
    if (!values || ![values.pred_method, values.model_id, values.structure_launch_variant].some(value => typeof value === 'string' && value.startsWith('esmfold2'))) return values;
    const document = values.complex_components;
    const rows = Array.isArray(document) ? document : document?.components ?? document?.sequences;
    if (!Array.isArray(rows) || (Array.isArray(document) && !rows.some(row => Array.isArray(row.id)))) return values;
    const settings = { ...values.esmfold2_component_settings };
    const components = rows.map(row => {
        if (!Array.isArray(row.id)) return row;
        const id = row.id[0];
        settings[id] = { ...esmfold2ComponentSettings(row), ...settings[id] };
        return { ...row, id, copy_ids: row.id };
    });
    // When the editor map is present all native annotations live in that map.
    for (const row of components) settings[row.id] ??= esmfold2ComponentSettings(row);
    return { ...values, complex_components: components.map(withoutEsmfold2Annotations), esmfold2_component_settings: settings,
        esmf_covalent_bonds: values.esmf_covalent_bonds ?? document?.covalent_bonds,
        esmf_distogram_conditioning: values.esmf_distogram_conditioning ?? document?.distogram_conditioning };
}

export function Esmfold2ChemistryControls({ components, settings, onChange, onComponentChange, bonds, onBondsChange, distograms, onDistogramsChange }: {
    components: ProtenixComponent[];
    onComponentChange: (key: string, patch: Partial<ProtenixComponent>) => void;
    settings: Record<string, Esmfold2ComponentSettings>;
    onChange: (key: string, value: Esmfold2ComponentSettings) => void;
    bonds?: Esmfold2Bond[]; onBondsChange: (value: Esmfold2Bond[]) => void;
    distograms?: Esmfold2Distogram[]; onDistogramsChange: (value: Esmfold2Distogram[]) => void;
}) {
    return <details><summary>ESMFold2 native chemistry and conditioning</summary>
        <p>ESMFold2 modification positions, residue indices and per-residue atom indices are zero-based. These drafts are independent of Boltz and Protenix; no positional conversion is performed. Native component types are protein, dna, rna and ligand. Pocket and template-file conditioning are not ESMFold2 inputs.</p>
        {components.map((component, i) => {
            const key = protenixEntityKey(component);
            const value = projectEsmfold2Component(component, settings[key]);
            const change = (patch: Partial<Esmfold2ComponentSettings>) => onChange(key, { modifications: value.modifications, msa: value.msa, msa_path: value.msa_path, ...patch, source_sequence: component.sequence });
            return <fieldset key={key}><legend>ESMFold2 entity {i + 1}: {component.name || component.id}</legend>
                <label>Copies<input aria-label={`ESMFold2 entity ${i + 1} count`} type="number" min={1} value={component.count ?? 1} onChange={e => onComponentChange(key, { count: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
                <label>Ordered copy IDs<input aria-label={`ESMFold2 entity ${i + 1} copy IDs`} value={component.copy_ids?.join(',') ?? ''} onChange={e => onComponentChange(key, { copy_ids: e.target.value === '' ? undefined : e.target.value.split(',').map(v => v.trim()) })} /></label>
                {['protein', 'dna', 'rna'].includes(component.type) && <>
                    {(value.modifications ?? []).map((mod, j) => <div key={j}>
                        <input aria-label={`ESMFold2 entity ${i + 1} modification ${j + 1} position`} type="number" min={0} value={mod.position} onChange={e => change({ modifications: value.modifications!.map((v, k) => j === k ? { ...mod, position: Number(e.target.value) } : v) })} />
                        <input aria-label={`ESMFold2 entity ${i + 1} modification ${j + 1} CCD`} value={mod.ccd} onChange={e => change({ modifications: value.modifications!.map((v, k) => j === k ? { ...mod, ccd: e.target.value } : v) })} />
                        <button type="button" onClick={() => change({ modifications: value.modifications!.filter((_, k) => j !== k) })}>Remove ESMFold2 modification {j + 1}</button>
                    </div>)}
                    <button type="button" onClick={() => change({ modifications: [...(value.modifications ?? []), { position: 0, ccd: '' }] })}>Add ESMFold2 modification to {component.id}</button>
                </>}
                {component.type === 'protein' && <>
                    <label>Inline MSA intent<select aria-label={`ESMFold2 entity ${i + 1} MSA mode`} value={value.msa === null ? 'none' : value.msa === undefined ? 'omitted' : 'inline'} onChange={e => change({ msa: e.target.value === 'none' ? null : e.target.value === 'inline' ? { sequences: [] } : undefined })}>
                        <option value="omitted">Omitted / provider policy</option><option value="none">Explicit null / no MSA</option><option value="inline">Inline sequences (including empty list)</option>
                    </select></label>
                    {value.msa && <label>Aligned sequences, one per line<textarea aria-label={`ESMFold2 entity ${i + 1} MSA sequences`} value={value.msa.sequences.join('\n')} onChange={e => change({ msa: { sequences: e.target.value === '' ? [] : e.target.value.split('\n') } })} /></label>}
                    <label>Native MSA path<input aria-label={`ESMFold2 entity ${i + 1} MSA path`} value={value.msa_path ?? ''} onChange={e => change({ msa_path: e.target.value || undefined })} /></label>
                </>}
                {component.type === 'ligand' && <label>Ordered CCD codes<input aria-label={`ESMFold2 entity ${i + 1} CCD`} value={Array.isArray(value.ccd) ? value.ccd.join(',') : value.ccd ?? ''} onChange={e => onComponentChange(key, { ccd: e.target.value === '' ? undefined : e.target.value.split(',').map(v => v.trim()) })} /></label>}
            </fieldset>;
        })}
        {(bonds ?? []).map((bond, i) => <fieldset key={i}><legend>ESMFold2 covalent bond {i + 1}</legend>
            {(['chain_id1', 'res_idx1', 'atom_idx1', 'chain_id2', 'res_idx2', 'atom_idx2'] as const).map(key => <label key={key}>{key}<input aria-label={`ESMFold2 bond ${i + 1} ${key}`} type={key.startsWith('chain') ? 'text' : 'number'} min={key.startsWith('chain') ? undefined : 0} value={bond[key]} onChange={e => onBondsChange(bonds!.map((v, j) => i === j ? { ...bond, [key]: key.startsWith('chain') ? e.target.value : Number(e.target.value) } : v))} /></label>)}
            <button type="button" onClick={() => onBondsChange(bonds!.filter((_, j) => i !== j))}>Remove ESMFold2 bond {i + 1}</button>
        </fieldset>)}
        <button type="button" onClick={() => onBondsChange([...(bonds ?? []), { chain_id1: '', res_idx1: 0, atom_idx1: 0, chain_id2: '', res_idx2: 0, atom_idx2: 0 }])}>Add ESMFold2 bond</button>
        {(distograms ?? []).map((item, i) => {
            const change = (patch: Partial<Esmfold2Distogram>) => onDistogramsChange(distograms!.map((v, j) => i === j ? { ...item, ...patch } : v));
            return <fieldset key={i}><legend>ESMFold2 distogram {i + 1}</legend>
                <input aria-label={`ESMFold2 distogram ${i + 1} chain`} value={item.chain_id} onChange={e => change({ chain_id: e.target.value })} />
                {item.distogram.map((row, r) => <div key={r}>{row.map((cell, c) => <input key={c} type="number" step="any" aria-label={`ESMFold2 distogram ${i + 1} row ${r + 1} column ${c + 1}`} value={cell} onChange={e => change({ distogram: item.distogram.map((v, j) => r === j ? v.map((n, k) => c === k ? Number(e.target.value) : n) : v) })} />)}
                    <button type="button" onClick={() => change({ distogram: item.distogram.map((v, j) => r === j ? [...v, 0] : v) })}>Add cell to distogram {i + 1} row {r + 1}</button>
                    <button type="button" onClick={() => change({ distogram: item.distogram.filter((_, j) => r !== j) })}>Remove distogram {i + 1} row {r + 1}</button>
                </div>)}
                <button type="button" onClick={() => change({ distogram: [...item.distogram, []] })}>Add row to distogram {i + 1}</button>
                <button type="button" onClick={() => onDistogramsChange(distograms!.filter((_, j) => i !== j))}>Remove ESMFold2 distogram {i + 1}</button>
            </fieldset>;
        })}
        <button type="button" onClick={() => onDistogramsChange([...(distograms ?? []), { chain_id: '', distogram: [] }])}>Add ESMFold2 distogram</button>
    </details>;
}
