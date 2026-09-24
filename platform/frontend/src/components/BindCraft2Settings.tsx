import { BindCraft2SourcePicker } from './BindCraft2SourcePicker';
import { useState, type ReactNode } from 'react';
import { BindCraft2ListEditor } from './BindCraft2ListEditor';
import { BC2Number, BC2PresetPicker } from './BindCraft2NativeControls';
import { BindCraft2MetricEditor } from './BindCraft2MetricEditor';
import { BC2CropEditor, BC2ModelPool, BC2SweepEditor } from './BindCraft2CollectionControls';
import { BC2_SYSTEM_FIELDS, BC2_PRIMARY, BC2_EXPERT_GROUPS, BC2_GROUP_CONTEXT, BC2_CHOICES, bc2ExpertGroup, bc2Label, bc2Help, bc2Range, bc2Unit } from '../lib/bindcraft2ControlMetadata';

/** Model-owned operator adapter. Discovery is the same inventory consumed by
 * services.bindcraft2_typed.validate_request; it does not enable the model. */
export type BC2Field = {
  native_key: string
  observed_types: string[]
  has_native_default: boolean
  native_default: unknown
  choices?: string[]
  runtime_fallback?: unknown
  applicable_when?: Record<string, unknown>
  fallback_authority?: string
  status: 'typed' | 'unresolved'
}
export type BC2Inventory = {
  upstream_commit: string
  fields: Record<string, BC2Field>
  presets: Record<string, Record<string, unknown>>
  paratope_conformations: string[]
  registered_metrics: Record<string, Record<string, { params: Record<string, { default_literal: unknown; source_default: string | null; request_types?: string[]; resolved_default?: unknown; native_default_encoding?: string; unresolved_reason?: string }> }>>
}
export type BC2Request = Record<string, unknown>
export type BC2Section = 'sources' | 'binder' | 'campaign' | 'objectives' | 'expert';
const internal = BC2_SYSTEM_FIELDS;
const selectors = new Set(['core', 'modality', 'target']);
const defaultSummary = (value: unknown) => value !== null && typeof value === 'object'
  ? Array.isArray(value) ? `${value.length} native entries (shown in controls)` : `${Object.keys(value).length} native entries (shown in controls)`
  : JSON.stringify(value)


export function BindCraft2Settings({ inventory, value, onChange, structureInputs, section }: {
  inventory: BC2Inventory; value: BC2Request; onChange: (next: BC2Request) => void; launchAvailable?: boolean; structureInputs?: ReactNode; section?: BC2Section
}) {
  const [search, setSearch] = useState('');
  const set = (key: string, next: unknown) => { const copy = { ...value }; if (next === undefined) delete copy[key]; else copy[key] = next; onChange(copy); };
  const control = (key: string, field: BC2Field) => {
    const type = field.observed_types[0]
    const current = Object.hasOwn(value, key) ? value[key] : (field.has_native_default ? field.native_default : undefined)
    const choices = field.choices ?? BC2_CHOICES[key];
    if (choices) return <select aria-label={key} value={current as string ?? ''} onChange={event => set(key, event.currentTarget.value || undefined)}>
      <option value="">Native / profile choice</option>{Array.from(new Set([...choices, ...(typeof current === 'string' ? [current] : [])])).map(choice => <option value={choice} key={choice}>{bc2Label(choice)}</option>)}
    </select>;
    if (selectors.has(key)) return <BC2PresetPicker label={key} value={current} singular choices={Object.keys(inventory.presets[key] ?? {}).filter(name => !(key === 'core' && ['default', 'reference'].includes(name)))} onChange={next => set(key, next)} />;
    if (key === 'paratope_conformations') return <BC2PresetPicker label={key} value={current} choices={inventory.paratope_conformations} onChange={next => set(key, next)} />;
    if (key === 'binder_lengths') return <div className="space-y-2"><BindCraft2ListEditor label={key} value={Array.isArray(current) ? current : []} onChange={next => set(key, next)} numericOnly /><small>Two values define an inclusive range; one or more than two define discrete lengths. Residues per binder copy; a scaffold overrides length selection.</small></div>;
    if (key === 'parameter_sweep') return <BC2SweepEditor inventory={inventory} value={value[key]} onChange={next => set(key, next)} />;
    if (key === 'multitarget_rounds_per_target') return <BC2PresetPicker label={key} value={current} choices={['screen', 'refine', 'anneal', 'harden', 'mutate']} onChange={next => set(key, next)} />;
    if (key === 'binder_shapes') {
      const groups = (current ?? []) as string[][]
      return <div>{groups.map((group, index) => <fieldset key={index}><legend>Conformation group {index + 1}</legend>
        {group.map((state, position) => <label key={position}>State {position + 1}<input aria-label={`binder_shapes.${index}.${position}`} value={state} onChange={event => set(key, groups.map((old, i) => i === index ? old.map((s, j) => j === position ? event.currentTarget.value : s) : old))} />
          <button type="button" onClick={() => set(key, groups.map((old, i) => i === index ? old.filter((_, j) => j !== position) : old))}>Remove state</button></label>)}
        <button type="button" onClick={() => set(key, groups.map((old, i) => i === index ? [...old, ''] : old))}>Add state</button>
        <button type="button" onClick={() => set(key, groups.filter((_, i) => i !== index))}>Remove group</button></fieldset>)}
        <button type="button" onClick={() => set(key, [...groups, ['']])}>Add conformation group</button></div>
    }
    if (key === 'crop_fasta_sequence') return <BC2CropEditor value={current} onChange={next => set(key, next)} />;
    if (key === 'design_models' || key === 'validation_models') return <BC2ModelPool label={key} value={current} onChange={next => set(key, next)} />;
    if (type === 'boolean') return <label className="!flex items-center gap-2"><input className="accent-[var(--accent-primary)]" aria-label={key} type="checkbox" checked={current === true} onChange={event => set(key, event.currentTarget.checked)} /><span className="text-sm">{current === true ? 'On' : current === false ? 'Off' : 'Native / profile'}</span></label>;
    if (type === 'number' || type === 'integer') return <BC2Number label={key} value={current} integer={type === 'integer'} range={bc2Range(key)} unit={bc2Unit(key)} onChange={next => set(key, next)} />;
    if (key === 'binder_scaffold') return <BindCraft2SourcePicker label={key} value={typeof current === 'string' ? current : ''} onChange={next => set(key, next)} structureOnly />
    if (type === 'string') return <input aria-label={key} type="text" value={current as string ?? ''} onChange={event => set(key, event.currentTarget.value)} />
    if (key === 'targets' && type === 'array') {
      const targets = (current ?? []) as Record<string, string | number>[]
      const update = (index: number, field: string, next: string | number) => set(key, targets.map((target, position) => position === index ? { ...target, [field]: next } : target))
      return <div>{targets.map((target, index) => <fieldset key={index}><legend>Target {index + 1}</legend>
        <BindCraft2SourcePicker label={`targets.${index}.target_path`} value={String(target.target_path ?? '')} onChange={next => update(index, 'target_path', next)} />
        {(['name', 'chains', 'hotspots', 'coldspots', 'objective', 'weight'] as const).map(field => <label key={field}>{field}<input aria-label={`targets.${index}.${field}`} type={field === 'weight' ? 'number' : 'text'} step={field === 'weight' ? 'any' : undefined}
          value={target[field] ?? ''} onChange={event => update(index, field, field === 'weight' ? Number(event.currentTarget.value) : event.currentTarget.value)} /></label>)}
        <button type="button" onClick={() => set(key, targets.filter((_, position) => position !== index))}>Remove target</button>
      </fieldset>)}<button type="button" onClick={() => set(key, [...targets, { name: '', target_path: '' }])}>Add target</button></div>
    }
    if (key === 'aa_bias' && type === 'object') {
      const bias = (current ?? {}) as Record<string, number>
      return <div className="grid grid-cols-4 gap-3 sm:grid-cols-5">{Array.from('ACDEFGHIKLMNPQRSTVWY').map(residue => <label key={residue}>{residue}<input aria-label={`aa_bias.${residue}`} type="number" step="any" value={bias[residue] ?? ''}
        onChange={event => { const next = { ...bias }; if (event.currentTarget.value === '') delete next[residue]; else next[residue] = Number(event.currentTarget.value); set(key, next) }} /></label>)}</div>
    }
    if ((key === 'filters' || key === 'losses') && type === 'object') return <BindCraft2MetricEditor kind={key} inventory={inventory} value={current} onChange={next => set(key, next)} />;
    if (type === 'array') return <BindCraft2ListEditor label={key} value={Array.isArray(current) ? current : []} onChange={next => set(key, next)} />;
    return <p className="text-sm text-[var(--text-secondary)]">Native type metadata is unavailable for this setting. Existing saved values are retained.</p>;
  }

  const delegated = new Set(structureInputs !== undefined ? ['targets', 'binder_scaffold'] : []);
  const sourceKeys = ['target', 'targets'];
  const primaryKeys = new Set([...sourceKeys, ...BC2_PRIMARY.flatMap(group => group.keys), 'losses', 'filters']);
  const fieldCard = (key: string) => {
    const field = inventory.fields[key];
    if (!field || internal.has(key) || delegated.has(key)) return null;
    const explicit = Object.hasOwn(value, key);
    const help = bc2Help(key);
    return <div key={key} data-bc2-field={key} className={`min-w-0 space-y-2 ${['targets', 'losses', 'filters', 'parameter_sweep', 'aa_bias', 'binder_shapes'].includes(key) ? 'col-span-full' : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-[var(--text-primary)]">{bc2Label(key)}</span>
        {explicit ? <button type="button" className="!border-0 !p-0 text-xs text-[var(--accent-primary)]" aria-label={`Reset ${key}`} onClick={() => set(key, undefined)}>Use native default</button> : <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--text-secondary)]">Native / profile</span>}
      </div>
      {help && <p className="text-xs leading-relaxed text-[var(--text-secondary)]">{help}</p>}
      {control(key, field)}
      {explicit && value[key] === null && <small>Explicit null is retained.</small>}
      <details className="text-xs text-[var(--text-secondary)]"><summary className="cursor-pointer">{explicit ? 'Override' : 'Default'} · native reference</summary>
        <code className="block mt-1 break-all">{key}</code>
        {field.has_native_default && <p>Native default: {defaultSummary(field.native_default)}. Selected presets may override this baseline.</p>}
        {!field.has_native_default && field.runtime_fallback !== undefined && <p>{field.fallback_authority ? 'Native relaxation fallback when omitted' : 'Native runtime fallback when omitted'}: {defaultSummary(field.runtime_fallback)}</p>}
        {field.applicable_when && <p>Applies when {Object.entries(field.applicable_when).map(([name, next]) => `${name} is ${next === true ? 'enabled' : String(next)}`).join(', ')}; omission retains native behavior.</p>}
        {!field.has_native_default && field.runtime_fallback === undefined && <p>No baseline default supplied. Omission leaves this to selected presets and native resolution.</p>}
      </details>
    </div>;
  };
  const grid = 'grid min-w-0 grid-cols-1 gap-x-6 gap-y-5 md:grid-cols-2';
  const expert = Object.keys(inventory.fields).filter(key => !internal.has(key) && !primaryKeys.has(key) && !delegated.has(key));
  return <section aria-label="BindCraft2 settings" className="space-y-5 [overflow-wrap:anywhere] text-[var(--text-primary)] [&_fieldset]:min-w-0 [&_fieldset]:space-y-2 [&_label]:block [&_small]:block [&_small]:text-xs [&_small]:text-[var(--text-secondary)] [&_input:not([type=checkbox]):not([type=range])]:w-full [&_input]:min-w-0 [&_input:not([type=checkbox]):not([type=range])]:rounded-lg [&_input:not([type=checkbox]):not([type=range])]:border [&_input:not([type=checkbox]):not([type=range])]:border-[var(--border-color)] [&_input:not([type=checkbox]):not([type=range])]:bg-[var(--bg-primary)] [&_input:not([type=checkbox]):not([type=range])]:px-3 [&_input:not([type=checkbox]):not([type=range])]:py-2 [&_select]:w-full [&_select]:min-w-0 [&_select]:rounded-lg [&_select]:border [&_select]:border-[var(--border-color)] [&_select]:bg-[var(--bg-primary)] [&_select]:p-2 [&_button]:max-w-full [&_button]:rounded-md [&_button]:border [&_button]:border-[var(--border-color)] [&_button]:px-2 [&_button]:py-1 [&_button]:text-sm [&_button:hover]:bg-[var(--bg-tertiary)] [&_button:disabled]:opacity-40 [&_input]:accent-[var(--accent-primary)] [&_input:focus-visible]:outline-[var(--accent-primary)] [&_select:focus-visible]:outline-[var(--accent-primary)]">
    <p className="text-xs leading-relaxed text-[var(--text-secondary)]">Native defaults are shown without writing them into the campaign. Explicit edits override presets; “Use native default” removes only that edit. Preview resolves the selected profiles.</p>
    <div hidden={section !== undefined && section !== 'sources'} className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-secondary)] p-4 sm:p-5">
      <h3 className="mb-1 font-semibold">Sources & structures</h3>
      <p className="mb-4 text-xs text-[var(--text-secondary)]">Choose a shipped target preset or prepare explicit target structures and sequences.</p>
      {structureInputs}
      <div className={`${grid} ${structureInputs !== undefined ? 'mt-4' : ''}`}>{sourceKeys.map(fieldCard)}</div>
    </div>
    {BC2_PRIMARY.map(group => group.keys.some(key => inventory.fields[key] && !delegated.has(key)) && <section hidden={section !== undefined && section !== (['Format & profiles', 'Binder design'].includes(group.title) ? 'binder' : 'campaign')} aria-label={group.title} key={group.title} className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-secondary)] p-4 sm:p-5">
      <div className="mb-4 border-l-2 border-[var(--accent-primary)] pl-3"><h3 className="font-semibold">{group.title}</h3><p className="mt-1 text-xs text-[var(--text-secondary)]">{group.help}</p></div>
      <div className={group.title === 'Design schedule' ? 'grid grid-cols-2 gap-4 lg:grid-cols-5' : grid}>{group.keys.map(fieldCard)}</div>
    </section>)}
    {(['losses', 'filters'] as const).map(key => inventory.fields[key] && <section hidden={section !== undefined && section !== 'objectives'} aria-label={bc2Label(key)} key={key} className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-secondary)] p-4 sm:p-5">{fieldCard(key)}</section>)}
    <section hidden={section !== undefined && section !== 'expert'} aria-label="Expert settings" className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-secondary)] p-4 sm:p-5">
      <h3 className="font-semibold">Expert settings</h3>
      <p className="mb-4 mt-1 text-xs text-[var(--text-secondary)]">All native scientific controls remain available. Groups describe context, not restrictions; inactive-mode overrides are kept when changing presets.</p>
      <input type="search" aria-label="Find expert setting" placeholder="Find a setting by scientific label or native key…" value={search} onChange={event => setSearch(event.currentTarget.value)} />
      <div className="mt-3 divide-y divide-[var(--border-color)]">
        {BC2_EXPERT_GROUPS.map(group => {
          const keys = expert.filter(key => bc2ExpertGroup(key) === group && `${key} ${bc2Label(key)} ${bc2Help(key) ?? ''}`.toLowerCase().includes(search.toLowerCase()));
          if (!keys.length) return null;
          const overridden = keys.filter(key => Object.hasOwn(value, key)).length;
          return <details key={group} open={search ? true : undefined} className="py-3"><summary className="cursor-pointer text-sm font-medium">{group}<span className="ml-2 text-xs font-normal text-[var(--text-secondary)]">{keys.length} controls{overridden ? ` · ${overridden} overrides` : ''}{group === 'Native relaxation' ? ` · ${value.relax_accepted_designs === true ? 'enabled' : 'optional accepted-design path'}` : ''}</span></summary>
            <div className="mt-4 space-y-4">{BC2_GROUP_CONTEXT[group] && <p className="text-xs text-[var(--text-secondary)]">{BC2_GROUP_CONTEXT[group]}</p>}<div className={grid}>{keys.map(fieldCard)}</div></div>
          </details>;
        })}
        {search && !expert.some(key => `${key} ${bc2Label(key)} ${bc2Help(key) ?? ''}`.toLowerCase().includes(search.toLowerCase())) && <p className="py-3 text-sm">No matching expert controls. Primary controls are above.</p>}
      </div>
    </section>
  </section>;
}
