/** Model-owned operator adapter. Discovery is the same inventory consumed by
 * services.bindcraft2_typed.validate_request; it does not enable the model. */
export type BC2Field = {
  native_key: string
  observed_types: string[]
  has_native_default: boolean
  native_default: unknown
  choices?: string[]
  runtime_fallback?: unknown
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
const internal = new Set(['project_folder', 'resume', 'gpu_ids', 'auto_multi_gpu', 'design_workers', 'workers_per_gpu', 'max_workers_per_gpu', 'worker_launch_stagger', 'compile_next_length'])
const selectors = new Set(['core', 'modality', 'target'])

export function BindCraft2Settings({ inventory, value, onChange }: {
  inventory: BC2Inventory; value: BC2Request; onChange: (next: BC2Request) => void
}) {
  const set = (key: string, next: unknown) => onChange({ ...value, [key]: next })
  const control = (key: string, field: BC2Field) => {
    const type = field.observed_types[0]
    const current = value[key] ?? (field.has_native_default ? field.native_default : undefined)
    if (field.choices) return <select aria-label={key} value={current as string ?? ''} onChange={event => set(key, event.currentTarget.value)}>
      <option value="">Select native choice</option>{field.choices.map(choice => <option value={choice} key={choice}>{choice}</option>)}
    </select>
    if (selectors.has(key)) return <select multiple aria-label={key} value={typeof current === 'string' ? [current] : Array.isArray(current) ? current as string[] : []}
      onChange={event => set(key, Array.from(event.currentTarget.selectedOptions, option => option.value))}>
      {Object.keys(inventory.presets[key] ?? {}).filter(name => !(key === 'core' && (name === 'default' || name === 'reference'))).map(name => <option key={name} value={name}>{name}</option>)}
    </select>
    if (key === 'paratope_conformations') return <select multiple aria-label={key} value={Array.isArray(current) ? current as string[] : []}
      onChange={event => set(key, Array.from(event.currentTarget.selectedOptions, option => option.value))}>
      {inventory.paratope_conformations.map(name => <option key={name} value={name}>{name}</option>)}
    </select>
    if (key === 'binder_lengths') {
      const lengths = (current ?? []) as number[]
      return <div>{lengths.map((length, index) => <label key={index}>Length {index + 1}<input aria-label={`binder_lengths.${index}`} type="number" min="1" step="1" value={length}
        onChange={event => set(key, lengths.map((item, position) => position === index ? Number(event.currentTarget.value) : item))} />
        <button type="button" onClick={() => set(key, lengths.filter((_, position) => position !== index))}>Remove length</button></label>)}
        <button type="button" onClick={() => set(key, [...lengths, 60])}>Add length</button><small>Two values define an inclusive range; other lengths are discrete.</small></div>
    }
    if (key === 'parameter_sweep') {
      const options = (current ?? {}) as Record<string, unknown>
      const update = (name: string, next: unknown) => set(key, { ...options, [name]: next })
      return <fieldset><legend>Native parameter sweep</legend>
        <label>Axes<select multiple aria-label="parameter_sweep.axes" value={options.axes as string[] ?? []}
          onChange={event => update('axes', Array.from(event.currentTarget.selectedOptions, option => option.value))}>
          {Object.entries(inventory.fields).filter(([name, field]) => !internal.has(name) && field.status === 'typed' && ['number', 'integer'].includes(field.observed_types[0])).map(([name]) => <option key={name} value={name}>{name}</option>)}
        </select></label>
        {(['max_arms', 'block_trajectories'] as const).map(name => <label key={name}>{name}<input aria-label={`parameter_sweep.${name}`} type="number" min="1" step="1" value={options[name] as number ?? ''} onChange={event => update(name, event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value))} /></label>)}
        <label>Levels (multipliers){((options.levels ?? []) as number[]).map((level, index) => <span key={index}><input aria-label={`parameter_sweep.levels.${index}`} type="number" min="0" step="any" value={level} onChange={event => update('levels', ((options.levels ?? []) as number[]).map((old, pos) => pos === index ? Number(event.currentTarget.value) : old))} /><button type="button" onClick={() => update('levels', ((options.levels ?? []) as number[]).filter((_, pos) => pos !== index))}>Remove level</button></span>)}<button type="button" onClick={() => update('levels', [...((options.levels ?? []) as number[]), 1])}>Add level</button></label>
        <label>Single multiplier<input aria-label="parameter_sweep.multiplier" type="number" min="0" step="any" value={options.multiplier as number ?? ''}
          onChange={event => update('multiplier', event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value))} /></label>
        <small>Native sweep divides max_trajectories among arms; compilation rejects an aggregate allowance above the requested limit.</small>
      </fieldset>
    }
    if (key === 'max_trajectories') return <input aria-label={key} type="number" min="1" step="1" required value={current as number ?? ''} onChange={event => set(key, event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value))} />
    if (type === 'boolean') return <input aria-label={key} type="checkbox" checked={Boolean(current)} onChange={event => set(key, event.currentTarget.checked)} />
    if (type === 'number' || type === 'integer') return <input aria-label={key} type="number" step={type === 'integer' ? '1' : 'any'} value={current as number ?? ''} onChange={event => set(key, event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value))} />
    if (type === 'string') return <input aria-label={key} type="text" value={current as string ?? ''} onChange={event => set(key, event.currentTarget.value)} />
    if (key === 'targets' && type === 'array') {
      const targets = (current ?? []) as Record<string, string | number>[]
      const update = (index: number, field: string, next: string | number) => set(key, targets.map((target, position) => position === index ? { ...target, [field]: next } : target))
      return <div>{targets.map((target, index) => <fieldset key={index}><legend>Target {index + 1}</legend>
        {(['name', 'target_path', 'chains', 'hotspots', 'coldspots', 'objective', 'weight'] as const).map(field => <label key={field}>{field}<input aria-label={`targets.${index}.${field}`} type={field === 'weight' ? 'number' : 'text'} step={field === 'weight' ? 'any' : undefined}
          value={target[field] ?? ''} onChange={event => update(index, field, field === 'weight' ? Number(event.currentTarget.value) : event.currentTarget.value)} /></label>)}
        <button type="button" onClick={() => set(key, targets.filter((_, position) => position !== index))}>Remove target</button>
      </fieldset>)}<button type="button" onClick={() => set(key, [...targets, { name: '', target_path: '' }])}>Add target</button></div>
    }
    if (key === 'aa_bias' && type === 'object') {
      const bias = (current ?? {}) as Record<string, number>
      return <div>{Array.from('ACDEFGHIKLMNPQRSTVWY').map(residue => <label key={residue}>{residue}<input aria-label={`aa_bias.${residue}`} type="number" step="any" value={bias[residue] ?? ''}
        onChange={event => { const next = { ...bias }; if (event.currentTarget.value === '') delete next[residue]; else next[residue] = Number(event.currentTarget.value); set(key, next) }} /></label>)}</div>
    }
    if ((key === 'filters' || key === 'losses') && type === 'object') {
      const entries = (current ?? {}) as Record<string, Record<string, unknown>>
      const update = (metric: string, entry: Record<string, unknown>) => set(key, { ...entries, [metric]: entry })
      return <div>{Object.entries(inventory.registered_metrics[key]).map(([metric, info]) => {
        const entry = entries[metric]
        return <fieldset key={metric}><legend>{metric}</legend>
          <label>Enable <input type="checkbox" aria-label={`${key}.${metric}.enabled`} checked={entry !== undefined} onChange={event => {
            if (event.currentTarget.checked) {
              const native = (inventory.fields[key]?.native_default as Record<string, Record<string, unknown>> | null)?.[metric]
              // Only a pinned native entry is a default; a new metric needs an explicit threshold.
              update(metric, native ? structuredClone(native) : key === 'filters' ? {} : { params: {} })
            } else { const copy = { ...entries }; delete copy[metric]; set(key, copy) }
          }} /></label>
          {entry && <>
            {key === 'filters' && <><label>Threshold <input type="number" step="any" aria-label={`${key}.${metric}.threshold`} value={entry.threshold as number ?? ''} onChange={event => { const next = { ...entry }; if (event.currentTarget.value === '') delete next.threshold; else next.threshold = Number(event.currentTarget.value); update(metric, next) }} /></label>
              {(['higher', 'mandatory'] as const).map(flag => <label key={flag}>{flag} (unset uses native behavior)<input type="checkbox" aria-label={`${key}.${metric}.${flag}`} checked={entry[flag] === true} onChange={event => update(metric, { ...entry, [flag]: event.currentTarget.checked })} /></label>)}</>}
            {Object.entries(info.params).map(([param, descriptor]) => {
              if (!descriptor.request_types) return <p key={param}>{param}: {descriptor.unresolved_reason ?? 'type unresolved'}</p>
              const defaultValue = descriptor.resolved_default ?? descriptor.default_literal
              const params = (entry.params ?? {}) as Record<string, unknown>
              const actual = Object.hasOwn(params, param) ? params[param] : defaultValue
              const write = (next: unknown) => update(metric, { ...entry, params: { ...params, [param]: next } })
              const nullable = descriptor.request_types.includes('null')
              const numeric = descriptor.request_types.includes('number') || descriptor.request_types.includes('integer')
              return <label key={param}>{param}{descriptor.native_default_encoding && <small> Native default: {descriptor.native_default_encoding}; explicit finite override only.</small>}
                {nullable && <select aria-label={`${key}.${metric}.params.${param}.mode`} value={actual === null ? 'null' : 'value'} onChange={event => write(event.currentTarget.value === 'null' ? null : '')}><option value="null">Native null</option><option value="value">Explicit value</option></select>}
                {descriptor.request_types.includes('boolean')
                  ? <input type="checkbox" aria-label={`${key}.${metric}.params.${param}`} checked={actual === true} onChange={event => write(event.currentTarget.checked)} />
                  : <input type={numeric ? 'number' : 'text'} step={numeric ? 'any' : undefined} aria-label={`${key}.${metric}.params.${param}`}
                    disabled={nullable && actual === null} value={actual === null || actual === undefined ? '' : actual as string | number}
                    onChange={event => { if (event.currentTarget.value === '') { const copy = { ...params }; delete copy[param]; update(metric, { ...entry, params: copy }) } else write(numeric ? Number(event.currentTarget.value) : event.currentTarget.value) }} />}</label>
            })}
          </>}
        </fieldset>
      })}</div>
    }
    return <span>Typed nested editor pending; this setting cannot be submitted from this form.</span>
  }
  return <section aria-label="BindCraft2 settings"><p>Native settings inventory only; model execution is not enabled. Unresolved settings block full parity.</p>
    {Object.entries(inventory.fields).filter(([key]) => !internal.has(key)).map(([key, field]) => {
      const supported = field.status === 'typed' && (selectors.has(key) || key === 'max_trajectories' ||
        ['boolean', 'number', 'integer', 'string'].includes(field.observed_types[0]) || ['filters', 'losses', 'targets', 'aa_bias', 'binder_lengths', 'paratope_conformations', 'parameter_sweep'].includes(key))
      return <div key={key}><label>{key}{field.has_native_default && <small> Native default: {JSON.stringify(field.native_default)}</small>}{!field.has_native_default && field.runtime_fallback !== undefined && <small> Native runtime fallback when omitted: {JSON.stringify(field.runtime_fallback)}</small>}
        {supported ? control(key, field) : <span> Unsupported typed control / unresolved source type</span>}</label></div>
    })}
  </section>
}
