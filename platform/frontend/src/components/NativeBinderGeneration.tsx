import { useState, type ReactNode } from 'react';
import { BinderWorkflowWorkspace } from './BinderWorkflowWorkspace';
import { NativeBinderSource } from './NativeBinderSource';
import { NativeCovalentBonds, NativeDatasetInput, NativeFrameworkIdentity, NativeSequenceTemplate } from './NativeGenerationSpecialControls';
import { nativeBinderField, nativeParameterKey, nativeParameterDisplay, nativeBinderSection, nativeBinderSections, type NativeBinderModel, type NativeBinderParameter } from '../lib/nativeBinderAuthoring';

const input = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 text-sm text-[var(--text-primary)]';
const labelFor = (parameter: NativeBinderParameter) => parameter.label || parameter.name.replace(/^boltzgen_/, '').replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
const sourceFields = new Set(['target_pdb', 'framework_pdb', 'scaffold_path', 'custom_framework_path', 'input_pdb', 'ligand_pdb']);

function CdrRanges({ value, onChange }: { value: unknown; onChange: (value: string) => void }) {
    const parts = typeof value === 'string' ? value.split(',') : [];
    const valid = parts.length > 0 && parts.length % 2 === 0 && parts.every((part, index) => index % 2 === 0 ? /^CDR[HL][123]$/.test(part) : /^\d+-\d+$/.test(part));
    return <div className="space-y-2">
        <input className={input} aria-label="cdr_length" value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} />
        {valid && <div className="grid gap-2 sm:grid-cols-2">{parts.filter((_, index) => index % 2 === 0).map((name, index) => {
            const bounds = parts[index * 2 + 1].split('-');
            return <fieldset className="rounded border border-[var(--border-primary)] p-2" key={`${name}:${index}`}><legend className="text-xs">{name} length (inclusive)</legend><div className="flex gap-2">{['Minimum', 'Maximum'].map((label, bound) => <label className="min-w-0 text-xs" key={label}>{label}<input className={input} aria-label={`${name} ${label.toLowerCase()}`} type="number" min={0} step={1} value={bounds[bound]} onChange={event => {
                if (event.target.value === '') return;
                const next = [...parts]; const range = [...bounds]; range[bound] = event.target.value; next[index * 2 + 1] = range.join('-'); onChange(next.join(','));
            }} /></label>)}</div></fieldset>;
        })}</div>}
        <p className="text-xs text-[var(--text-secondary)]">The native range string is preserved exactly. Heavy-only sampling retains any saved light-loop ranges without treating them as active chains.</p>
    </div>;
}

/** Native values are controlled by the parent draft. Null never means default. */
export function NativeSetting({ parameter, values, onPatch, chains, onBrowse, displayValues = values }: { parameter: NativeBinderParameter; values: Record<string, UntypedApiValue>; onPatch: (patch: Record<string, UntypedApiValue>) => void; chains: string[]; onBrowse?: (field: string) => void; displayValues?: Record<string, UntypedApiValue> }) {
    const key = parameter.name;
    const nativeKey = nativeBinderField(key);
    const value = Object.hasOwn(values, key) ? values[key] : parameter.default;
    const display = nativeParameterDisplay(parameter, displayValues);
    const change = (value: unknown) => onPatch({ [key]: value });
    const numeric = ['integer', 'number'].includes(parameter.type);
    const chainRole = /(?:^|_)chains?$/.test(key);
    const text = typeof value === 'string' || typeof value === 'number' ? value : '';
    const array = Array.isArray(value) ? parameter.type === 'array' || parameter.accepted_types?.includes('array') : parameter.type === 'array' && !(typeof value === 'string' && parameter.accepted_types?.includes('string'));
    const unavailable = parameter.read_only || parameter.ui_control === 'unavailable' || Boolean(parameter.unavailable_reason);
    const slider = !unavailable && numeric && parameter.ui_control === 'slider' && Number.isFinite(parameter.minimum) && Number.isFinite(parameter.maximum);
    return <div className={`min-w-0 space-y-2 ${['cdr_length', 'covalent_bonds', 'binder_sequence', 'nanobody_framework', 'sabdab_framework'].includes(nativeKey) ? 'col-span-full' : ''}`} data-native-setting={key} data-native-applicable={display.applicable}>
        <label htmlFor={`native-${key}`} className="block text-sm font-medium">{labelFor(parameter)}{display.required ? ' * (required)' : ''}{parameter.units ? ` (${parameter.units})` : ''}</label>
        {(parameter.display_help || parameter.description) && <p className="text-xs text-[var(--text-secondary)]">{parameter.display_help || parameter.description}</p>}
        {parameter.required_help && <p className="text-xs text-[var(--text-secondary)]">{parameter.required_help}</p>}
        {!display.applicable && <p className="text-xs text-[var(--text-secondary)]">Inactive for the current selection; saved value retained.</p>}
        {unavailable ? <div className="space-y-2"><p className="text-sm">{parameter.unavailable_reason || 'Not editable in this native mode.'}</p>
            {Object.hasOwn(values, key) && value !== null && value !== undefined && <><p>Historical requested value: <code>{JSON.stringify(value)}</code></p><button type="button" onClick={() => change(null)}>Clear incompatible {labelFor(parameter).toLowerCase()}</button></>}
            {value === null && <p className="text-xs">Native null (no threshold).</p>}
        </div>
            : key === 'cdr_length' ? <CdrRanges value={value} onChange={change} />
            : ['binder_sequence', 'nanobody_framework'].includes(nativeKey) ? <NativeSequenceTemplate name={key} value={value} onChange={change} />
            : nativeKey === 'covalent_bonds' ? <NativeCovalentBonds value={value} onChange={change} />
            : nativeKey === 'sabdab_framework' ? <NativeFrameworkIdentity value={value} onChange={change} />
            : parameter.type === 'file' ? <NativeDatasetInput name={key} value={value} onChange={change} onBrowse={onBrowse ? () => onBrowse(key) : undefined} />
            : parameter.type === 'boolean' ? <label className="flex items-center gap-2 text-sm"><input id={`native-${key}`} aria-label={key} type="checkbox" checked={value === true} onChange={event => change(event.target.checked)} />{value === null ? 'Native null' : value === undefined ? 'Not specified' : value ? 'Enabled' : 'Disabled'}</label>
            : parameter.enum ? <select id={`native-${key}`} aria-label={key} className={input} value={text} onChange={event => { const option = parameter.enum!.find(item => String(item) === event.target.value); change(option ?? event.target.value); }}><option value="" disabled>{value === null ? 'Native null' : 'Select value'}</option>{parameter.enum.map(option => <option key={String(option)} value={option}>{String(option)}</option>)}</select>
            : array ? <div className="space-y-2">{(Array.isArray(value) ? value : []).map((item: unknown, index: number) => <div className="flex gap-2" key={index}><input className={input} aria-label={`${key} ${index + 1}`} value={String(item)} onChange={event => { const next = [...value]; next[index] = event.target.value; change(next); }} /><button type="button" aria-label={`Remove ${key} ${index + 1}`} onClick={() => change(value.filter((_: unknown, i: number) => i !== index))}>Remove</button></div>)}<button type="button" onClick={() => change([...(Array.isArray(value) ? value : []), ''])}>Add {labelFor(parameter).toLowerCase()}</button></div>
            : <><input id={`native-${key}`} aria-label={key} className={input} type={numeric ? 'number' : 'text'} value={text} min={parameter.minimum} max={parameter.maximum} step={parameter.type === 'integer' ? 1 : 'any'} list={chainRole ? `native-${key}-chains` : undefined} placeholder={parameter.ui_placeholder} onChange={event => change(numeric ? event.target.value === '' ? '' : Number(event.target.value) : event.target.value)} />{chainRole && <datalist id={`native-${key}-chains`}>{chains.map(chain => <option key={chain} value={chain} />)}</datalist>}</>}
        {slider && typeof value === 'number' && <input aria-label={`${key} slider`} className="w-full accent-accent" type="range" min={parameter.minimum} max={parameter.maximum} step={parameter.step ?? (parameter.type === 'integer' ? 1 : 'any')} value={value} onChange={event => change(Number(event.target.value))} />}
        <details className="text-xs text-[var(--text-secondary)]"><summary>Native field details</summary><div className="mt-2 flex flex-wrap gap-3">
            <code>{parameter.native_path || parameter.name}</code>
            {Object.hasOwn(parameter, 'default') && <span>Native default: {parameter.default === null ? 'null' : typeof parameter.default === 'object' ? 'see native contract' : String(parameter.default)}</span>}
            {(parameter.minimum !== undefined || parameter.maximum !== undefined) && <span>Range: {parameter.minimum ?? 'unbounded'} – {parameter.maximum ?? 'unbounded'}</span>}
            {parameter.accepted_types && <span>Accepted: {parameter.accepted_types.join(' or ')}. The saved representation is preserved; clear explicitly before choosing another representation.</span>}
        </div></details>
        {!unavailable && !display.required && display.nullable && <button className="text-xs text-[var(--text-secondary)]" type="button" onClick={() => change(null)}>Use native null</button>}
        {!unavailable && parameter.accepted_types?.includes('array') && !Array.isArray(value) && (value === '' || value === null || value === undefined) && <button type="button" onClick={() => change([])}>Use list representation</button>}
        {!unavailable && parameter.accepted_types?.includes('string') && Array.isArray(value) && value.length === 0 && <button type="button" onClick={() => change('')}>Use text representation</button>}
    </div>;
}

export function NativeBinderGeneration({ model, mode, parameters, values, onPatch, profile, nativeBehavior, onBrowse, generationSlot, onSectionChange }: {
    model: NativeBinderModel; mode: string; parameters: NativeBinderParameter[];
    values: Record<string, UntypedApiValue>; onPatch: (patch: Record<string, UntypedApiValue>) => void;
    profile?: unknown; nativeBehavior?: unknown; onBrowse?: (field: string) => void;
    generationSlot?: ReactNode; onSectionChange?: (section: string) => void;
}) {
    const sections = nativeBinderSections(model);
    const [active, setActive] = useState(sections[0].id);
    const [sourceChains, setSourceChains] = useState<Record<string, string[]>>({});
    const onChains = (field: string, chains: string[]) => setSourceChains(previous => ({ ...previous, [nativeBinderField(field)]: chains }));
    const displayValues = Object.fromEntries(parameters.flatMap(parameter => {
        const key = nativeParameterKey(parameter, values);
        const value = Object.hasOwn(values, key) ? values[key] : parameter.default;
        return [[parameter.name, value], [nativeBinderField(parameter.name), value]];
    }));
    const controls = parameters.map(parameter => ({ ...parameter, name: nativeParameterKey(parameter, values) }));
    const render = (parameter: NativeBinderParameter) => {
        const display = nativeParameterDisplay(parameter, displayValues);
        const field = nativeBinderField(parameter.name);
        return sourceFields.has(field)
            ? <div className="col-span-full" key={parameter.name} data-native-setting={parameter.name}>
                <p className="text-sm font-medium">{labelFor(parameter)}{display.required ? ' * (required)' : ''}</p>
                {(parameter.display_help || parameter.description) && <p className="mb-2 text-xs text-[var(--text-secondary)]">{parameter.display_help || parameter.description}</p>}
                {parameter.required_help && <p className="mb-2 text-xs text-[var(--text-secondary)]">{parameter.required_help}</p>}
                {!display.applicable && <p className="text-xs">Inactive for the current selection; source retained.</p>}
                <NativeBinderSource model={model} mode={mode} field={parameter.name} label={field === 'target_pdb' ? model === 'ppiflow' && mode !== 'protein_binder' ? 'Antigen' : 'Target' : field === 'framework_pdb' ? 'Framework' : 'Scaffold'} values={values} onPatch={onPatch} onChains={onChains} />
            </div>
            : <NativeSetting key={parameter.name} parameter={parameter} values={values} displayValues={displayValues} onPatch={onPatch} onBrowse={onBrowse} chains={sourceChains[/heavy_chain|light_chain/.test(parameter.name) ? 'framework_pdb' : field === 'scaffold_chain' ? 'scaffold_path' : 'target_pdb'] || []} />;
    };
    const principal = new Set(['num_designs', 'samples_per_target', 'dataset_seed', 'seed', 'num_timesteps', 'batch_size', 'alpha', 'budget']);
    const groupsFor = (section: string) => {
        const fields = controls.filter(parameter => nativeBinderSection(model, parameter) === section);
        const primary = fields.filter(parameter => section !== 'Expert' && (section !== 'Generation' && section !== 'Native selection' || principal.has(nativeBinderField(parameter.name))));
        const groups = new Map<string, NativeBinderParameter[]>();
        for (const parameter of fields.filter(parameter => !primary.includes(parameter))) {
            const group = parameter.ui_group || parameter.group || (section === 'Generation' ? 'Sampling schedule' : section === 'Native selection' ? 'Native ranking and selection' : 'Optimization and runtime');
            groups.set(group, [...(groups.get(group) || []), parameter]);
        }
        return { primary, groups };
    };
    const current = (name: string) => values[name] ?? values[`boltzgen_${name === 'target_pdb' ? 'target_pdb_path' : name}`];
    return <BinderWorkflowWorkspace title={model === 'ppiflow' ? 'PPIFlow initial generation' : 'BoltzGen generation'}
        description={model === 'ppiflow' ? 'Native target-conditioned backbone generation. Antibody and nanobody modes use a separate framework; historical partial-flow refinement remains a separate editor.' : 'Native binder generation with separate target context and optional sequence or structural templates. Protein and peptide modes are not VHH presets.'}
        sections={sections} activeSection={active} onSectionChange={section => { setActive(section); onSectionChange?.(section); }}
        summary={<span>{model} / {mode} · target: {current('target_pdb') || 'not specified'}{values.framework_pdb ? ` · framework: ${values.framework_pdb}` : ''}{current('scaffold_path') ? ` · scaffold: ${current('scaffold_path')}` : ''} · {values.samples_per_target ?? current('num_designs') ?? 'native'} requested samples</span>}>
        {model === 'ppiflow' && <p className="text-sm text-[var(--text-secondary)]" data-native-required-summary>Required for the current native source choice: {controls.filter(parameter => nativeParameterDisplay(parameter, displayValues).required).map(labelFor).join(', ') || 'see source alternatives and field help'}. Source alternatives and conditional requirements are shown beside each input.</p>}
        {sections.map(section => <div key={section.id} hidden={active !== section.id} className="space-y-5" aria-label={section.label}>
            {section.id === 'Sources' && <p className="text-sm text-[var(--text-secondary)]">Native target binding positions and scaffold design ranges are 1-indexed chain-local positions. Legacy binding-site residues use their separately declared author-PDB grammar. Inspection never silently converts between them.</p>}
            {section.id === 'Generation' && <p className="text-sm text-[var(--text-secondary)]">Choose the output count and sampling first. Additional schedule and runtime settings stay available below.</p>}
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">{groupsFor(section.id).primary.map(render)}</div>
            {[...groupsFor(section.id).groups].map(([group, fields]) => <details key={group}><summary className="cursor-pointer font-medium">{group.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())}</summary><div className="mt-4 grid grid-cols-1 gap-5 lg:grid-cols-2">{fields.map(render)}</div></details>)}
            {section.id === 'Generation' && generationSlot}
            {section.id === 'Expert' && (profile !== undefined || nativeBehavior !== undefined) && <details><summary>Native profile and behavior (read-only)</summary><p className="text-xs text-[var(--text-secondary)]">Checkpoint-owned architecture and native behavior are discovery metadata, not editable sampling overrides.</p><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify({ profile, native_behavior: nativeBehavior }, null, 2)}</pre></details>}
            {!parameters.some(parameter => nativeBinderSection(model, parameter) === section.id) && <p className="text-sm text-[var(--text-secondary)]">No {section.label.toLowerCase()} controls advertised for this native mode.</p>}
        </div>)}
    </BinderWorkflowWorkspace>;
}
