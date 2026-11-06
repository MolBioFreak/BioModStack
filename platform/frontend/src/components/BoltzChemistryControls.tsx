import type { ProtenixComponent } from './protenixChemistry';
import { protenixEntityKey } from './protenixChemistry';

// Native Boltz dialect: do not reinterpret Protenix PTMs or entity ordinals.
type Token = [string, number | string];
type Atom = [string, number, string];
export type BoltzConstraint = { bond: { atom1: Atom; atom2: Atom } }
    | { contact: { token1: Token; token2: Token; max_distance?: number; force?: boolean } }
    | { pocket: { binder: string; contacts: Token[]; max_distance?: number; force?: boolean } };
export type BoltzProperty = { affinity: { binder: string } };
type Component = Omit<ProtenixComponent, 'ccd' | 'msa' | 'modifications'> & {
    ccd?: string | string[]; msa?: string | { precomputed_msa_dir?: string }; cyclic?: boolean;
    modifications?: Array<{ position: number; ccd: string } | NonNullable<ProtenixComponent['modifications']>[number]>;
};
export function BoltzChemistryControls({ components, onComponentChange, constraints, onConstraintsChange, properties, onPropertiesChange, seed, onSeedChange }: {
    components: ProtenixComponent[]; onComponentChange: (key: string, patch: Partial<ProtenixComponent>) => void;
    constraints?: BoltzConstraint[]; onConstraintsChange: (value: BoltzConstraint[]) => void;
    properties?: BoltzProperty[]; onPropertiesChange: (value: BoltzProperty[]) => void;
    seed: string; onSeedChange: (value: string) => void;
}) {
    const change = (c: Component, patch: Partial<Component>) => onComponentChange(protenixEntityKey(c as ProtenixComponent), patch as Partial<ProtenixComponent>);
    const token = (label: string, value: Token | Atom, update: (value: any) => void) => <span>
        <input aria-label={`${label} chain`} value={value[0]} onChange={e => update([e.target.value, ...value.slice(1)])} />
        <input aria-label={`${label} residue or atom`} value={value[1]} onChange={e => update([value[0], /^\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value, ...value.slice(2)])} />
        {value.length === 3 && <input aria-label={`${label} atom`} value={value[2]} onChange={e => update([value[0], value[1], e.target.value])} />}
    </span>;
    return <details><summary>Boltz native chemistry and constraints</summary>
        <p>Boltz uses chain/copy IDs and position/CCD modifications. Other model annotations are retained without conversion. Residue positions are 1-based; review endpoints after sequence or copy-ID edits.</p>
        <label>Boltz random seed (optional)<input aria-label="Boltz seed" type="number" value={seed} onChange={e => onSeedChange(e.target.value)} /></label>
        {(components as Component[]).map((c, i) => <fieldset key={protenixEntityKey(c as ProtenixComponent)}><legend>Boltz entity {i + 1}: {c.name || c.id}</legend>
            <input aria-label={`Boltz entity ${i + 1} name`} value={c.name ?? ''} onChange={e => change(c, { name: e.target.value })} />
            <label>Copies<input aria-label={`Boltz entity ${i + 1} count`} type="number" min={1} value={c.count ?? 1} onChange={e => change(c, { count: e.target.value === '' ? undefined : Number(e.target.value) })} /></label>
            <label>Ordered copy IDs<input aria-label={`Boltz entity ${i + 1} copy IDs`} value={c.copy_ids?.join(',') ?? ''} onChange={e => change(c, { copy_ids: e.target.value === '' ? undefined : e.target.value.split(',').map(v => v.trim()) })} /></label>
            {['protein', 'peptide', 'dna', 'rna'].includes(c.type) && <>
                <label>Cyclic<input aria-label={`Boltz entity ${i + 1} cyclic`} type="checkbox" checked={c.cyclic ?? false} onChange={e => change(c, { cyclic: e.target.checked })} /></label>
                {(c.modifications ?? []).map((m, j) => 'position' in m ? <div key={j}>
                    <input aria-label={`Boltz entity ${i + 1} modification ${j + 1} position`} type="number" min={1} value={m.position} onChange={e => change(c, { modifications: c.modifications!.map((v, k) => k === j ? { ...m, position: Number(e.target.value) } : v) })} />
                    <input aria-label={`Boltz entity ${i + 1} modification ${j + 1} CCD`} value={m.ccd} onChange={e => change(c, { modifications: c.modifications!.map((v, k) => k === j ? { ...m, ccd: e.target.value } : v) })} />
                    <button type="button" onClick={() => change(c, { modifications: c.modifications!.filter((_, k) => k !== j) })}>Remove Boltz modification {j + 1}</button>
                </div> : <p key={j}>Retained other-model modification; edit in its native controls.</p>)}
                <button type="button" onClick={() => change(c, { modifications: [...(c.modifications ?? []), { position: 1, ccd: '' }] })}>Add Boltz modification to {c.id}</button>
            </>}
            {['protein', 'peptide'].includes(c.type) && <>
                <label>MSA<select aria-label={`Boltz entity ${i + 1} MSA mode`} value={typeof c.msa === 'string' ? c.msa === 'empty' ? 'empty' : c.msa === '' ? 'auto' : 'path' : 'inherit'} onChange={e => change(c, { msa: e.target.value === 'inherit' ? undefined : e.target.value === 'empty' ? 'empty' : '' })}>
                    <option value="inherit">Inherited / omitted</option><option value="auto">Native automatic (blank)</option><option value="empty">Explicit no MSA</option><option value="path">Supplied path</option>
                </select></label>
                <input aria-label={`Boltz entity ${i + 1} MSA path`} value={typeof c.msa === 'string' && c.msa !== 'empty' ? c.msa : ''} onChange={e => change(c, { msa: e.target.value })} />
            </>}
            {c.type === 'ligand' && <label>CCD code or ordered CCD list<input aria-label={`Boltz entity ${i + 1} CCD`} value={Array.isArray(c.ccd) ? c.ccd.join(',') : c.ccd ?? ''} onChange={e => change(c, { ccd: e.target.value.includes(',') ? e.target.value.split(',').map(v => v.trim()) : e.target.value })} /></label>}
        </fieldset>)}
        {(constraints ?? []).map((constraint, i) => {
            const update = (next: BoltzConstraint) => onConstraintsChange(constraints!.map((c, j) => j === i ? next : c));
            return <fieldset key={i}><legend>Boltz constraint {i + 1}</legend>
                {'bond' in constraint && <>{token(`Boltz bond ${i + 1} atom1`, constraint.bond.atom1, atom1 => update({ bond: { ...constraint.bond, atom1 } }))}{token(`Boltz bond ${i + 1} atom2`, constraint.bond.atom2, atom2 => update({ bond: { ...constraint.bond, atom2 } }))}</>}
                {'contact' in constraint && <>{token(`Boltz contact ${i + 1} token1`, constraint.contact.token1, token1 => update({ contact: { ...constraint.contact, token1 } }))}{token(`Boltz contact ${i + 1} token2`, constraint.contact.token2, token2 => update({ contact: { ...constraint.contact, token2 } }))}</>}
                {'pocket' in constraint && <><input aria-label={`Boltz pocket ${i + 1} binder`} value={constraint.pocket.binder} onChange={e => update({ pocket: { ...constraint.pocket, binder: e.target.value } })} />
                    {constraint.pocket.contacts.map((c, j) => <div key={j}>{token(`Boltz pocket ${i + 1} contact ${j + 1}`, c, next => update({ pocket: { ...constraint.pocket, contacts: constraint.pocket.contacts.map((v, k) => k === j ? next : v) } }))}<button type="button" onClick={() => update({ pocket: { ...constraint.pocket, contacts: constraint.pocket.contacts.filter((_, k) => k !== j) } })}>Remove pocket contact {j + 1}</button></div>)}
                    <button type="button" onClick={() => update({ pocket: { ...constraint.pocket, contacts: [...constraint.pocket.contacts, ['', 1]] } })}>Add pocket contact</button></>}
                {('contact' in constraint || 'pocket' in constraint) && (() => { const key = 'contact' in constraint ? 'contact' : 'pocket'; const value = 'contact' in constraint ? constraint.contact : constraint.pocket; return <>
                    <label>Maximum distance (Å)<input aria-label={`Boltz constraint ${i + 1} max distance`} type="number" value={value.max_distance ?? ''} onChange={e => update({ [key]: { ...value, max_distance: e.target.value === '' ? undefined : Number(e.target.value) } } as BoltzConstraint)} /></label>
                    <label>Force<input aria-label={`Boltz constraint ${i + 1} force`} type="checkbox" checked={value.force ?? false} onChange={e => update({ [key]: { ...value, force: e.target.checked } } as BoltzConstraint)} /></label>
                </>; })()}
                <button type="button" onClick={() => onConstraintsChange(constraints!.filter((_, j) => j !== i))}>Remove Boltz constraint {i + 1}</button>
            </fieldset>;
        })}
        <button type="button" onClick={() => onConstraintsChange([...(constraints ?? []), { bond: { atom1: ['', 1, ''], atom2: ['', 1, ''] } }])}>Add Boltz bond</button>
        <button type="button" onClick={() => onConstraintsChange([...(constraints ?? []), { contact: { token1: ['', 1], token2: ['', 1] } }])}>Add Boltz contact</button>
        <button type="button" onClick={() => onConstraintsChange([...(constraints ?? []), { pocket: { binder: '', contacts: [] } }])}>Add Boltz pocket</button>
        {(properties ?? []).map((property, i) => <label key={i}>Affinity ligand chain<input aria-label={`Boltz affinity ${i + 1} ligand`} value={property.affinity.binder} onChange={e => onPropertiesChange(properties!.map((p, j) => i === j ? { affinity: { binder: e.target.value } } : p))} /><button type="button" onClick={() => onPropertiesChange(properties!.filter((_, j) => i !== j))}>Remove affinity {i + 1}</button></label>)}
        <button type="button" onClick={() => onPropertiesChange([...(properties ?? []), { affinity: { binder: '' } }])}>Add explicit affinity ligand</button>
    </details>;
}
