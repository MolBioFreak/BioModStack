import { useState } from 'react';
import { NativeStructureSource, OrderedRowActions, type NativeAuthoringProps } from './CalibyNativeForm';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';

// NativeOptions shape bindings only: no scientific defaults, validation or normalization.
const tokens = ['ALA', 'CYS', 'ASP', 'GLU', 'PHE', 'GLY', 'HIS', 'ILE', 'LYS', 'LEU', 'MET', 'ASN', 'PRO', 'GLN', 'ARG', 'SER', 'THR', 'VAL', 'TRP', 'TYR', 'UNK'];
const inputClass = 'rounded border border-slate-600 bg-slate-900 p-2 text-sm';
type AtomKind = 'residue' | 'chain' | 'token' | 'ccd' | 'number';
type Axis = 'residue' | 'token';
const listKinds: Record<string, AtomKind> = { remove_ccds: 'ccd', fixed_residues: 'residue', designed_residues: 'residue', fixed_chains: 'chain', designed_chains: 'chain', omit: 'token' };
const groupKinds: Record<string, AtomKind> = { symmetry_residues: 'residue', symmetry_residues_weights: 'number', homo_oligomer_chains: 'chain' };
const biasAxes: Record<string, Axis[]> = {
    bias: ['token'], bias_per_residue: ['residue', 'token'], pair_bias: ['token', 'token'],
    pair_bias_per_residue_pair: ['residue', 'residue', 'token', 'token'], temperature_per_residue: ['residue'],
};
const nullableScalars = new Set(['design_seed', 'remove_waters', 'occupancy_threshold_sidechain', 'occupancy_threshold_backbone', 'structure_noise', 'decode_type', 'causality_pattern', 'initialize_sequence_embedding_with_ground_truth', 'atomize_side_chains', 'temperature']);

function Atom({ label, kind, value, onChange }: { label: string; kind: AtomKind; value: UntypedApiValue; onChange: (value: UntypedApiValue) => void }) {
    return kind === 'token' ? <select aria-label={label} className={inputClass} value={value ?? ''} onChange={event => onChange(event.target.value)}><option value="">Choose amino acid</option>{tokens.map(token => <option key={token}>{token}</option>)}</select>
        : <input aria-label={label} className={inputClass} type={kind === 'number' ? 'number' : 'text'} step={kind === 'number' ? 'any' : undefined} placeholder={kind === 'residue' ? 'Author residue ID' : kind === 'chain' ? 'Author chain ID' : kind === 'ccd' ? 'CCD name' : 'Value'} value={value ?? ''} onChange={event => onChange(kind === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value)} />;
}
function NativeList({ label, kind, value, onChange }: { label: string; kind: AtomKind; value: UntypedApiValue[]; onChange: (value: UntypedApiValue[]) => void }) {
    return <div role="group" aria-label={label} className="space-y-2">{value.map((item, index) => <div key={index} className="flex flex-wrap gap-2"><Atom label={`${label}.${index}`} kind={kind} value={item} onChange={next => onChange(value.map((old, i) => i === index ? next : old))} /><OrderedRowActions label={`${label}.${index}`} rows={value} index={index} onChange={onChange} /></div>)}<button type="button" onClick={() => onChange([...value, ''])}>Add {label} entry</button></div>;
}

/** Explicit native bias axes, not a generic arbitrary-object editor. */
function BiasTable({ label, axes, value, onChange }: { label: string; axes: Axis[]; value: Record<string, UntypedApiValue>; onChange: (value: Record<string, UntypedApiValue>) => void }) {
    const [newKey, setNewKey] = useState('');
    return <div role="group" aria-label={label} className="space-y-2 border-l border-slate-600 pl-3">
        <p className="text-xs">{axes[0] === 'residue' ? 'Author residue IDs (chain, number and insertion code)' : 'Native amino-acid tokens'}</p>
        {Object.entries(value).map(([key, item]) => <div key={key} className="space-y-2"><span>{key}</span>
            {axes.length === 1 ? <Atom label={`${label}.${key}`} kind="number" value={item} onChange={next => onChange({ ...value, [key]: next })} /> : <BiasTable label={`${label}.${key}`} axes={axes.slice(1)} value={item} onChange={next => onChange({ ...value, [key]: next })} />}
            <button type="button" aria-label={`Remove ${label}.${key}`} onClick={() => onChange(Object.fromEntries(Object.entries(value).filter(([old]) => old !== key)))}>Remove {key}</button>
        </div>)}
        <Atom label={`${label}.new-key`} kind={axes[0]} value={newKey} onChange={setNewKey} />
        <button type="button" disabled={!newKey || Object.hasOwn(value, newKey)} onClick={() => { onChange({ ...value, [newKey]: axes.length === 1 ? '' : {} }); setNewKey(''); }}>Add {label} key</button>
    </div>;
}
function ResidueOmissions({ value, onChange }: { value: Record<string, string[]>; onChange: (value: Record<string, string[]>) => void }) {
    const [residue, setResidue] = useState('');
    return <div>{Object.entries(value).map(([key, list]) => <fieldset key={key}><legend>{key}</legend><NativeList label={`omit_per_residue.${key}`} kind="token" value={list} onChange={next => onChange({ ...value, [key]: next })} /><button type="button" onClick={() => onChange(Object.fromEntries(Object.entries(value).filter(([old]) => old !== key)))}>Remove omit_per_residue.{key}</button></fieldset>)}
        <Atom label="omit_per_residue.new-key" kind="residue" value={residue} onChange={setResidue} /><button type="button" disabled={!residue || Object.hasOwn(value, residue)} onClick={() => { onChange({ ...value, [residue]: [] }); setResidue(''); }}>Add omit_per_residue key</button>
    </div>;
}

export function LigandMPNNDesignForm({ parameters, values, onChange, renderScalar }: NativeAuthoringProps) {
    const render = (parameter: UntypedApiValue) => {
        const name: string = parameter.name;
        const value = values[name];
        const change = (next: UntypedApiValue) => onChange(name, next);
        const structured = name in listKinds || name in groupKinds || name in biasAxes || name === 'omit_per_residue';
        if (name === 'target_pdb' || name === 'ligand_pdb') return <><p className="text-xs">{parameter.description}</p><NativeStructureSource label={name} value={value} onChange={change} /></>;
        if (structured) return <fieldset className="space-y-2" data-native-option={name}><legend>{parameter.label || name}</legend><p className="text-xs">{parameter.description}</p>
            <div className="flex gap-3"><button type="button" onClick={() => change(null)}>Set {name} null</button><button type="button" onClick={() => change(name in listKinds || name in groupKinds ? [] : {})}>Set {name} empty</button><span>{value === null ? 'null' : value === undefined ? 'Not supplied' : ''}</span></div>
            {value != null && (name in listKinds ? <NativeList label={name} kind={listKinds[name]} value={value} onChange={change} />
                : name in biasAxes ? <BiasTable label={name} axes={biasAxes[name]} value={value} onChange={change} />
                : name === 'omit_per_residue' ? <ResidueOmissions value={value} onChange={change} />
                : <div>{value.map((group: UntypedApiValue[], index: number) => <fieldset key={index}><legend>Group {index + 1}</legend><NativeList label={`${name}.${index}`} kind={groupKinds[name]} value={group} onChange={next => change(value.map((old: UntypedApiValue, i: number) => i === index ? next : old))} /><OrderedRowActions label={`${name}.${index}`} rows={value} index={index} onChange={change} /></fieldset>)}<button type="button" onClick={() => change([...value, []])}>Add {name} group</button></div>)}
        </fieldset>;
        if (nullableScalars.has(name)) return <div data-native-option={name} className="space-y-2"><label className="block">{parameter.label || name}</label><p className="text-xs">{parameter.description}</p>
            {parameter.type === 'boolean' ? <select className={inputClass} aria-label={name} value={value == null ? String(value) : String(Boolean(value))} onChange={event => change(event.target.value === 'null' ? null : event.target.value === 'true')}><option value="undefined" disabled>Not supplied</option><option value="null">null — native behavior</option><option value="false">false</option><option value="true">true</option></select>
                : parameter.enum ? <select className={inputClass} aria-label={name} value={value ?? ''} onChange={event => change(event.target.value)}><option value="" disabled>{value === null ? 'null' : 'Not supplied'}</option>{parameter.enum.map((option: string) => <option key={option}>{option}</option>)}</select>
                : <Atom label={name} kind="number" value={value} onChange={change} />}
            <button type="button" onClick={() => change(null)}>Set {name} null</button><span className="text-xs">{value === null ? 'null — native behavior' : ''}</span>
        </div>;
        return renderScalar(parameter);
    };
    return <section aria-label="LigandMPNN native authoring" className="space-y-3"><p>Supply one assembled protein-context structure. Labels are annotations, not positioned coordinates. Native residue and chain identities are retained verbatim.</p><SequenceDesignerSettings fields={parameters} renderField={render} /></section>;
}
