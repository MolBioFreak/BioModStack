import { uploadFile, fetchModelById } from '../lib/api';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { protenixEntityKey, type ProtenixComponent, type ProtenixBond, type ProtenixConstraint } from './protenixChemistry';

const style = 'bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm w-full';
type FieldSchema = { description?: string; default?: string | number; pattern?: string };
function Field({ label, value, onChange, numeric = false, multiline = false, schema, step = 1 }: {
    label: string; value: string | number | null | undefined; onChange: (value: string | number | undefined) => void; numeric?: boolean; multiline?: boolean; schema?: FieldSchema; step?: number | 'any';
}) {
    return <label title={schema?.description} className="block text-xs text-slate-300">{label}{multiline
        ? <textarea aria-label={label} className={style} value={value ?? ''} onChange={e => onChange(e.target.value)} />
        : <input aria-label={label} placeholder={schema?.default === undefined ? undefined : `Native default: ${schema.default}`} pattern={schema?.pattern} className={style} type={numeric ? 'number' : 'text'} step={numeric ? step : undefined} value={value ?? ''}
            onChange={e => onChange(numeric ? (e.target.value === '' ? undefined : Number(e.target.value)) : e.target.value)} />}</label>;
}

export function ProtenixChemistryControls({ components, onComponentChange, bonds, onBondsChange, constraint, onConstraintChange }: {
    components: ProtenixComponent[]; onComponentChange: (id: string, patch: Partial<ProtenixComponent>) => void;
    bonds?: ProtenixBond[]; onBondsChange: (bonds: ProtenixBond[]) => void;
    constraint?: ProtenixConstraint; onConstraintChange: (constraint: ProtenixConstraint) => void;
}) {
    const [fileError, setFileError] = useState('');
    // Reuse the public model/mode discovery owner; metadata never enters params.
    const model = useQuery({ queryKey: ['model', 'protenix'], queryFn: () => fetchModelById('protenix') });
    const schema = model.data?.data?.modes?.find((mode: { id: string }) => mode.id === 'complex')?.parameter_schema;
    const componentFields = schema?.$defs?.Component?.properties;

    return <section aria-label="Protenix chemistry" className="space-y-4 border-t border-slate-700 pt-4">
        <h3>Protenix molecular chemistry</h3>
        {schema && <p className="text-xs text-slate-400">Native contract: {schema.schema_name} v{schema.schema_version}</p>}
        <p className="text-xs text-slate-400">Positions and entity/copy numbers are 1-based. Entity order below is the native bond order. BMS labels are independent of native copy IDs. CCD substitutions require CCD_ identifiers. Sequence edits clear alignments, not chemistry; review positions after changing a sequence.</p>
        {components.map((component, entity) => {
            const change = (patch: Partial<ProtenixComponent>) => onComponentChange(protenixEntityKey(component), patch);
            const nucleic = component.type === 'dna' || component.type === 'rna';
            const polymer = nucleic || component.type === 'protein' || component.type === 'peptide';
            return <details key={`${protenixEntityKey(component)}:${entity}`} open className="space-y-2"><summary>Entity {entity + 1}: {component.id} ({component.type})</summary>
                <div className="grid grid-cols-2 gap-2">
                    <Field label={`Entity ${entity + 1} ID`} value={component.id} onChange={value => change({ id: String(value ?? '') })} />
                    <Field label={`Entity ${entity + 1} name`} value={component.name} onChange={value => change({ name: String(value ?? '') })} />
                    <Field label={`Entity ${entity + 1} count`} schema={componentFields?.count} numeric value={component.count} onChange={value => change({ count: value as number | undefined })} />
                    <Field label={`Entity ${entity + 1} copy IDs (comma separated)`} value={component.copy_ids?.join(',')} onChange={value => change({ copy_ids: String(value).split(',').map(id => id.trim()) })} />
                    <button type="button" onClick={() => change({ copy_ids: undefined })}>Omit entity {entity + 1} copy IDs</button>
                </div>
                <p className="text-xs">Count is an integer; explicit native copy IDs must contain one unique ID per copy. Omitted count uses the native default.</p>
                {polymer && <div>
                    <Field label={`Entity ${entity + 1} sequence`} value={component.sequence} multiline onChange={value => change({ sequence: String(value ?? '') })} />
                    {(component.modifications ?? []).map((modification, index) => {
                        const positionKey = nucleic ? 'basePosition' : 'ptmPosition';
                        const typeKey = nucleic ? 'modificationType' : 'ptmType';
                        const row = modification as unknown as Record<string, string | number>;
                        const update = (key: string, value: string | number | undefined) => change({ modifications: component.modifications!.map((item, i) => i === index ? { ...item, [key]: value } : item) });
                        return <div key={index} className="grid grid-cols-3 gap-2">
                            <Field label={`Entity ${entity + 1} modification ${index + 1} position`} numeric value={row[positionKey]} onChange={v => update(positionKey, v)} />
                            <Field label={`Entity ${entity + 1} modification ${index + 1} CCD`} schema={schema?.$defs?.[nucleic ? 'BaseModification' : 'ProteinModification']?.properties?.[typeKey]} value={row[typeKey]} onChange={v => update(typeKey, v)} />
                            <button type="button" onClick={() => change({ modifications: component.modifications!.filter((_, i) => i !== index) })}>Remove modification</button>
                        </div>;
                    })}
                    <button type="button" onClick={() => change({ modifications: [...component.modifications ?? [], nucleic ? { basePosition: 1, modificationType: 'CCD_' } : { ptmPosition: 1, ptmType: 'CCD_' }] })}>Add modification to entity {entity + 1}</button>
                </div>}
                <details><summary>Native alignment, template and ligand inputs</summary>
                    {(component.type === 'protein' || component.type === 'peptide' || component.type === 'rna') && <>
                        {(component.type === 'rna' ? ['unpairedMsa', 'unpairedMsaPath'] as const : ['pairedMsa', 'unpairedMsa', 'pairedMsaPath', 'unpairedMsaPath', 'msa_path', 'templatesPath'] as const).map(key => <div key={key}><Field label={`Entity ${entity + 1} ${key}`} value={component[key]} multiline={key === 'pairedMsa' || key === 'unpairedMsa'} onChange={value => change({ [key]: value })} /><button type="button" onClick={() => change({ [key]: undefined })}>Omit {key}</button></div>)}
                        {component.type !== 'rna' && <><Field label={`Entity ${entity + 1} legacy MSA directory`} value={component.msa?.precomputed_msa_dir} onChange={value => change({ msa: { ...component.msa, precomputed_msa_dir: String(value) } })} /><button type="button" onClick={() => change({ msa: undefined })}>Omit legacy MSA directory</button></>}
                        <p className="text-xs">templatesPath is a managed HHR/A3M template-hit file, not a coordinate directory. Empty inline MSA is explicit; Omit restores absence.</p>
                    </>}
                    {(component.type === 'ligand' || component.type === 'ion') && <>
                        {(component.type === 'ion' ? ['ccd', 'ion', 'element'] as const : ['ccd', 'smiles', 'ligand_file'] as const).map(key => <Field key={key} label={`Entity ${entity + 1} ${key}`} value={component[key]} onChange={value => change(component.type === 'ligand' ? { ccd: undefined, smiles: undefined, ligand_file: undefined, [key]: value || undefined } : { [key]: value || undefined })} />)}
                        {component.type === 'ligand' && <label>Upload managed ligand conformer<input aria-label={`Entity ${entity + 1} ligand upload`} type="file" accept=".mol,.mol2,.sdf,.pdb" onChange={async e => {
                            const file = e.target.files?.[0]; if (!file) return;
                            try { const response = await uploadFile('inputs/structure_prediction', file); change({ ccd: undefined, smiles: undefined, ligand_file: response.data.path }); setFileError(''); }
                            catch (error) { setFileError(error instanceof Error ? error.message : 'Ligand upload failed'); }
                        }} /></label>}
                    </>}
                </details>
            </details>;
        })}
        {fileError && <p role="alert">{fileError}</p>}
        <h4>Covalent bonds</h4>
        {(bonds ?? []).map((bond, index) => <div key={index} className="grid grid-cols-4 gap-2">
            {(['left', 'right'] as const).flatMap((side, sideIndex) => (['entity', 'position', 'atom', 'copy'] as const).map(part => {
                const canonical = `${side}_${part}` as keyof ProtenixBond;
                const legacy = `${part}${sideIndex + 1}` as keyof ProtenixBond;
                const key = canonical in bond || !(legacy in bond) ? canonical : legacy;
                return <Field key={canonical} label={`Bond ${index + 1} ${side} ${part}`} value={bond[key]} numeric={part !== 'atom'} onChange={value => onBondsChange(bonds!.map((item, i) => i === index ? { ...item, [key]: value } : item))} />;
            }))}
            <button type="button" onClick={() => onBondsChange(bonds!.filter((_, i) => i !== index))}>Remove bond {index + 1}</button>
        </div>)}
        <button type="button" onClick={() => onBondsChange([...bonds ?? [], {}])}>Add covalent bond</button>
        <details><summary>Native contact and pocket constraints</summary>
            {(constraint?.contact ?? []).map((contact, index) => <div key={index} className="grid grid-cols-4 gap-2">
                {(['entity1', 'copy1', 'position1', 'atom1', 'entity2', 'copy2', 'position2', 'atom2', 'min_distance', 'max_distance'] as const).map(key => <Field key={key} label={`Contact ${index + 1} ${key}`} value={contact[key]} numeric={!key.startsWith('atom')} step={key.endsWith('distance') ? 'any' : 1} onChange={value => onConstraintChange({ ...constraint, contact: constraint!.contact!.map((item, i) => i === index ? { ...item, [key]: value } : item) })} />)}
                <button type="button" onClick={() => onConstraintChange({ ...constraint, contact: constraint!.contact!.filter((_, i) => i !== index) })}>Remove contact {index + 1}</button>
            </div>)}
            <button type="button" onClick={() => onConstraintChange({ ...constraint, contact: [...constraint?.contact ?? [], {}] })}>Add contact</button>
            {constraint?.pocket ? <div>
                {(['entity', 'copy'] as const).map(key => <Field key={key} label={`Pocket binder ${key}`} numeric value={constraint.pocket!.binder_chain[key]} onChange={value => onConstraintChange({ ...constraint, pocket: { ...constraint.pocket!, binder_chain: { ...constraint.pocket!.binder_chain, [key]: value } } })} />)}
                <Field label="Pocket max distance" numeric step="any" value={constraint.pocket.max_distance} onChange={value => onConstraintChange({ ...constraint, pocket: { ...constraint.pocket!, max_distance: value as number } })} />
                {constraint.pocket.contact_residues.map((residue, index) => <div key={index}>
                    {(['entity', 'copy', 'position'] as const).map(key => <Field key={key} label={`Pocket residue ${index + 1} ${key}`} numeric value={residue[key]} onChange={value => onConstraintChange({ ...constraint, pocket: { ...constraint.pocket!, contact_residues: constraint.pocket!.contact_residues.map((item, i) => i === index ? { ...item, [key]: value } : item) } })} />)}
                    <button type="button" onClick={() => onConstraintChange({ ...constraint, pocket: { ...constraint.pocket!, contact_residues: constraint.pocket!.contact_residues.filter((_, i) => i !== index) } })}>Remove pocket residue {index + 1}</button>
                </div>)}
                <button type="button" onClick={() => onConstraintChange({ ...constraint, pocket: { ...constraint.pocket!, contact_residues: [...constraint.pocket!.contact_residues, {}] } })}>Add pocket residue</button>
                <button type="button" onClick={() => onConstraintChange({ ...constraint, pocket: undefined })}>Remove pocket</button>
            </div> : <button type="button" onClick={() => onConstraintChange({ ...constraint, pocket: { binder_chain: {}, contact_residues: [] } })}>Add pocket</button>}
        </details>
    </section>;
}
