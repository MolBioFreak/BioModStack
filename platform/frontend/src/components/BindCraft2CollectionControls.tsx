import { useState } from 'react';
import type { BC2Inventory } from './BindCraft2Settings';
import { BC2Number, BC2PresetPicker } from './BindCraft2NativeControls';
import { BindCraft2ListEditor } from './BindCraft2ListEditor';
import { BC2_SYSTEM_FIELDS } from '../lib/bindcraft2ControlMetadata';

export function BC2CropEditor({ value, onChange }: { value: unknown; onChange: (next: unknown) => void }) {
  const [draftRange, setDraftRange] = useState(false);
  const [start, setStart] = useState<number | undefined>();
  const [end, setEnd] = useState<number | undefined>();
  const range = Array.isArray(value) || draftRange;
  const mode = range ? 'range' : value === false ? 'off' : 'length';
  return <div className="space-y-2"><select aria-label="crop_fasta_sequence.mode" value={mode} onChange={event => {
    const mode = event.currentTarget.value; setDraftRange(mode === 'range');
    if (mode !== 'range') onChange(mode === 'off' ? false : undefined);
  }}><option value="off">Full sequence (no crop)</option><option value="length">Exact window length</option><option value="range">Window length range</option></select>
    {range ? <div className="grid grid-cols-2 gap-2">
      <label>Minimum<BC2Number label="crop_fasta_sequence.0" integer unit="residues" value={Array.isArray(value) ? value[0] : start} onChange={next => { setStart(next); const upper = Array.isArray(value) ? value[1] : end; if (next !== undefined && upper !== undefined) onChange([next, upper]); }} /></label>
      <label>Maximum<BC2Number label="crop_fasta_sequence.1" integer unit="residues" value={Array.isArray(value) ? value[1] : end} onChange={next => { setEnd(next); const lower = Array.isArray(value) ? value[0] : start; if (next !== undefined && lower !== undefined) onChange([lower, next]); }} /></label>
      {!Array.isArray(value) && <small className="col-span-2">Enter both lengths to apply a range. The current request is unchanged until both are supplied.</small>}
    </div> : value !== false && <BC2Number label="crop_fasta_sequence.length" integer unit="residues" value={value} onChange={onChange} />}
  </div>;
}

export function BC2ModelPool({ label, value, onChange }: { label: string; value: unknown; onChange: (next: unknown) => void }) {
  const [draft, setDraft] = useState('');
  const [entryType, setEntryType] = useState('string');
  const validDraft = draft !== '' && (entryType === 'string' || Number.isInteger(Number(draft)));
  return <div className="space-y-2"><select aria-label={`${label}.mode`} value={Array.isArray(value) ? 'named' : 'count'} onChange={event => onChange(event.currentTarget.value === 'named' ? [] : undefined)}><option value="count">Model count</option><option value="named">Exact names / model indices</option></select>
    {Array.isArray(value) ? <div role="group" aria-label={label} className="space-y-2">
      {value.map((model, index) => <div key={index} className="flex items-center gap-2">
        {typeof model === 'number' ? <BC2Number label={`${label}.${index}`} integer value={model} onChange={next => { if (next !== undefined) onChange(value.map((old, i) => i === index ? next : old)); }} /> : <input aria-label={`${label}.${index}`} value={String(model)} onChange={event => onChange(value.map((old, i) => i === index ? event.currentTarget.value : old))} />}
        <button type="button" aria-label={`Remove ${label}.${index}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove</button>
      </div>)}
      <select aria-label={`${label}.new.type`} value={entryType} onChange={event => setEntryType(event.currentTarget.value)}><option value="string">Exact model name</option><option value="integer">Native pool index</option></select>
      <input aria-label={`${label}.new`} value={draft} onChange={event => setDraft(event.currentTarget.value)} />
      <button type="button" aria-label={`Add ${label}`} disabled={!validDraft} onClick={() => { if (validDraft) { onChange([...value, entryType === 'integer' ? Number(draft) : draft]); setDraft(''); } }}>Add model</button>
    </div> : <BC2Number label={`${label}.count`} integer value={value} onChange={onChange} />}
    {Array.isArray(value) && <small>Native names include model_1_ptm, model_2_ptm and model_1_multimer_v3 through model_5_multimer_v3. Integer indices remain integers.</small>}
  </div>;
}

export function BC2SweepEditor({ inventory, value, onChange }: { inventory: BC2Inventory; value: unknown; onChange: (next: unknown) => void }) {
  const options = (typeof value === 'object' && value !== null ? value : {}) as Record<string, unknown>;
  const mode = value === undefined ? 'native' : value === false ? 'off' : value === true ? 'on' : 'custom';
  const update = (name: string, next: unknown) => { const copy = { ...options }; if (next === undefined) delete copy[name]; else copy[name] = next; onChange(copy); };
  return <fieldset className="grid gap-4 md:grid-cols-2"><legend>Native parameter sweep</legend>
    <label className="md:col-span-2">Sweep mode<select aria-label="parameter_sweep.mode" value={mode} onChange={event => onChange(event.currentTarget.value === 'native' ? undefined : event.currentTarget.value === 'off' ? false : event.currentTarget.value === 'on' ? true : {})}>
      <option value="native">Native / profile (omitted)</option><option value="off">Disabled (false)</option><option value="on">Enabled with native options (true)</option><option value="custom">Explicit options</option>
    </select></label>
    {mode === 'custom' && <>
    <div className="md:col-span-2"><span className="text-sm">Sweep axes</span><BC2PresetPicker label="parameter_sweep.axes" value={options.axes} choices={Object.entries(inventory.fields).filter(([name, field]) => !BC2_SYSTEM_FIELDS.has(name) && field.status === 'typed' && field.observed_types.length > 0 && field.observed_types.every(type => ['number', 'integer'].includes(type))).map(([name]) => name)} onChange={next => update('axes', next)} /></div>
    {(['max_arms', 'block_trajectories'] as const).map(name => <label key={name}>{name === 'max_arms' ? 'Maximum arms' : 'Trajectories per block'}<BC2Number label={`parameter_sweep.${name}`} integer value={options[name]} onChange={next => update(name, next)} /></label>)}
    <div><span>Explicit multiplier levels</span><BindCraft2ListEditor label="parameter_sweep.levels" value={Array.isArray(options.levels) ? options.levels : []} numericOnly onChange={next => update('levels', next)} />{Object.hasOwn(options, 'levels') && <button type="button" aria-label="Reset parameter_sweep.levels" onClick={() => update('levels', undefined)}>Use native levels</button>}</div>
    <label>Single multiplier<BC2Number label="parameter_sweep.multiplier" value={options.multiplier} onChange={next => update('multiplier', next)} /><small>Choose explicit levels or a single multiplier, not both. Clear the unused override to restore native resolution.</small></label>
    <small className="md:col-span-2">Native sweep divides the trajectory allowance among arms. Axis order and exact multipliers are preserved.</small>
    </>}
  </fieldset>;
}
