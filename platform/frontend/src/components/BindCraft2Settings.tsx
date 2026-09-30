/** Model-owned operator adapter. Discovery is the same inventory consumed by
 * services.bindcraft2_typed.validate_request; it does not enable the model. */
export type BC2Field = {
  native_key: string
  observed_types: string[]
  has_native_default: boolean
  native_default: unknown
  status: 'typed' | 'unresolved'
}
export type BC2Inventory = {
  upstream_commit: string
  fields: Record<string, BC2Field>
  presets: Record<string, Record<string, unknown>>
  paratope_conformations: string[]
  registered_metrics: Record<string, Record<string, { params: Record<string, { default_literal: unknown; resolved_default?: unknown; request_type?: string; source_default: string | null }> }>>
}
export type BC2Request = Record<string, unknown>
const internal = new Set(['project_folder', 'resume', 'gpu_ids', 'auto_multi_gpu', 'design_workers', 'workers_per_gpu', 'max_workers_per_gpu', 'worker_launch_stagger', 'compile_next_length'])
const selectors = new Set(['core', 'modality', 'target'])

export function BindCraft2Settings({ inventory, value, onChange, launchAvailable }: {
  inventory: BC2Inventory; value: BC2Request; onChange: (next: BC2Request) => void; launchAvailable?: boolean
}) {
  const set = (key: string, next: unknown) => onChange({ ...value, [key]: next })
  const numberList = (key: string, current: unknown) => {
    const values = Array.isArray(current) ? current as (number | '')[] : []
    return <div>{values.map((item, index) => <label key={index}>{key} {index + 1}<input type="number" step="1" min="1" aria-label={`${key}.${index}`} value={item}
      onChange={event => set(key, values.map((old, position) => position === index ? Number(event.currentTarget.value) : old))} />
      <button type="button" onClick={() => set(key, values.filter((_, position) => position !== index))}>Remove</button></label>)}
      <button type="button" onClick={() => set(key, [...values, ''])}>Add {key}</button></div>
  }
  const control = (key: string, field: BC2Field) => {
    const type = field.observed_types[0]
    const current = value[key] ?? (field.has_native_default ? field.native_default : undefined)
    if (selectors.has(key)) return <select multiple aria-label={key} value={typeof current === 'string' ? [current] : Array.isArray(current) ? current as string[] : []}
      onChange={event => set(key, Array.from(event.currentTarget.selectedOptions, option => option.value))}>
      {Object.keys(inventory.presets[key] ?? {}).filter(name => !(key === 'core' && (name === 'default' || name === 'reference'))).map(name => <option key={name} value={name}>{name}</option>)}
    </select>
    if (key === 'paratope_conformations') return <select multiple aria-label={key} value={Array.isArray(current) ? current as string[] : []}
      onChange={event => set(key, Array.from(event.currentTarget.selectedOptions, option => option.value))}>
      {inventory.paratope_conformations.map(name => <option key={name} value={name}>{name}</option>)}
    </select>
    if (key === 'binder_lengths') {
      const lengths = (current ?? []) as (number | '')[]
      return <div>{lengths.map((length, index) => <label key={index}>Length {index + 1}<input aria-label={`binder_lengths.${index}`} type="number" min="1" step="1" value={length}
        onChange={event => set(key, lengths.map((item, position) => position === index ? Number(event.currentTarget.value) : item))} />
        <button type="button" onClick={() => set(key, lengths.filter((_, position) => position !== index))}>Remove length</button></label>)}
        <button type="button" onClick={() => set(key, [...lengths, ''])}>Add length</button><small>Two values define an inclusive range; other lengths are discrete.</small></div>
    }
    if (key === 'multitarget_rounds_per_target') return <select multiple aria-label={key} value={Array.isArray(current) ? current as string[] : []}
      onChange={event => set(key, Array.from(event.currentTarget.selectedOptions, option => option.value))}>
      {['screen', 'refine', 'anneal', 'harden', 'mutate'].map(stage => <option key={stage} value={stage}>{stage}</option>)}
    </select>
    if (key === 'binder_shapes') {
      const groups = (current ?? []) as string[][]
      return <div>{groups.map((group, index) => <fieldset key={index}><legend>Conformation group {index + 1}</legend>
        {group.map((state, position) => <label key={position}>State {position + 1}<input aria-label={`binder_shapes.${index}.${position}`} value={state} onChange={event => set(key, groups.map((old, i) => i === index ? old.map((s, j) => j === position ? event.currentTarget.value : s) : old))} />
          <button type="button" onClick={() => set(key, groups.map((old, i) => i === index ? old.filter((_, j) => j !== position) : old))}>Remove state</button></label>)}
        <button type="button" onClick={() => set(key, groups.map((old, i) => i === index ? [...old, ''] : old))}>Add state</button>
        <button type="button" onClick={() => set(key, groups.filter((_, i) => i !== index))}>Remove group</button></fieldset>)}
        <button type="button" onClick={() => set(key, [...groups, ['']])}>Add conformation group</button></div>
    }
    if (key === 'crop_fasta_sequence') return <div><label>Crop mode<select aria-label="crop_fasta_sequence.mode" value={current === false ? 'off' : Array.isArray(current) ? 'range' : 'length'} onChange={event => set(key, event.currentTarget.value === 'off' ? false : event.currentTarget.value === 'range' ? ['', ''] : undefined)}><option value="off">Off</option><option value="length">Exact length</option><option value="range">Range</option></select></label>
      {Array.isArray(current) ? numberList(key, current) : current !== false && <input aria-label="crop_fasta_sequence.length" type="number" min="1" step="1" value={current as number ?? ''} onChange={event => set(key, Number(event.currentTarget.value))} />}</div>
    if (key === 'validation_models') return <div><label>Selection<select aria-label="validation_models.mode" value={Array.isArray(current) ? 'named' : 'count'} onChange={event => set(key, event.currentTarget.value === 'named' ? [] : undefined)}><option value="count">Count</option><option value="named">Model names</option></select></label>
      {Array.isArray(current) ? <div>{current.map((model, index) => <label key={index}>Model {index + 1}<input aria-label={`validation_models.${index}`} value={model} onChange={event => set(key, current.map((old, position) => position === index ? event.currentTarget.value : old))} /><button type="button" onClick={() => set(key, current.filter((_, position) => position !== index))}>Remove</button></label>)}<button type="button" onClick={() => set(key, [...current, ''])}>Add model</button></div> : <input aria-label="validation_models.count" type="number" min="1" step="1" value={current as number ?? ''} onChange={event => set(key, Number(event.currentTarget.value))} />}</div>
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
            if (event.currentTarget.checked) update(metric, (field.native_default as Record<string, Record<string, unknown>> | null)?.[metric] ?? (key === 'filters' ? {} : { params: {} }))
            else { const copy = { ...entries }; delete copy[metric]; set(key, copy) }
          }} /></label>
          {entry && <>
            {key === 'filters' && <><label>Threshold <input type="number" step="any" aria-label={`${key}.${metric}.threshold`} value={entry.threshold as number ?? ''} onChange={event => update(metric, { ...entry, threshold: event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value) })} /></label>
              <label>Direction<select aria-label={`${key}.${metric}.higher`} value={entry.higher === undefined ? '' : entry.higher ? 'higher' : 'lower'} onChange={event => update(metric, { ...entry, higher: event.currentTarget.value === 'higher' })}><option value="">Choose</option><option value="higher">At least</option><option value="lower">At most</option></select></label>
              <label>Mandatory<input type="checkbox" aria-label={`${key}.${metric}.mandatory`} checked={entry.mandatory === true} onChange={event => update(metric, { ...entry, mandatory: event.currentTarget.checked })} /></label></>}
            <label>Prediction state<input aria-label={`${key}.${metric}.prediction_state`} value={entry.prediction_state as string ?? ''} onChange={event => update(metric, { ...entry, prediction_state: event.currentTarget.value })} /></label>
            {Object.entries(info.params).map(([param, descriptor]) => {
              const defaultValue = descriptor.default_literal
              const resolved = descriptor.resolved_default ?? defaultValue
              if (descriptor.request_type === 'nullable') return <p key={param}>{param}: native null; non-null input unqualified</p>
              const params = (entry.params ?? {}) as Record<string, unknown>
              if (descriptor.request_type === 'number' && typeof resolved === 'object') return <label key={param}>{param} (native default: infinity)<input type="number" step="any" aria-label={`${key}.${metric}.params.${param}`} value={params[param] as number ?? ''} onChange={event => update(metric, { ...entry, params: { ...params, [param]: event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value) } })} /></label>
              if (descriptor.request_type === 'string' && resolved === null) return <label key={param}>{param} (native default: null)<input type="text" aria-label={`${key}.${metric}.params.${param}`} value={params[param] as string ?? ''} onChange={event => update(metric, { ...entry, params: { ...params, [param]: event.currentTarget.value } })} /></label>
              if (resolved === null || typeof resolved === 'object') return <p key={param}>{param}: source type not editable here</p>
              const actual = params[param] ?? resolved
              const write = (next: unknown) => update(metric, { ...entry, params: { ...params, [param]: next } })
              return <label key={param}>{param}{typeof resolved === 'boolean'
                ? <input type="checkbox" aria-label={`${key}.${metric}.params.${param}`} checked={Boolean(actual)} onChange={event => write(event.currentTarget.checked)} />
                : <input type={typeof resolved === 'number' ? 'number' : 'text'} step="any" aria-label={`${key}.${metric}.params.${param}`} value={actual as string | number} onChange={event => write(typeof resolved === 'number' ? Number(event.currentTarget.value) : event.currentTarget.value)} />}</label>
            })}
          </>}
        </fieldset>
      })}</div>
    }
    return <span>Typed nested editor pending; this setting cannot be submitted from this form.</span>
  }
  return <section aria-label="BindCraft2 settings"><p>{launchAvailable === true ? 'Launcher reports execution available.' : launchAvailable === false ? 'Model execution is not enabled.' : 'Launch availability is determined by the launcher.'} Unresolved settings prevent a full parity claim.</p>
    {Object.entries(inventory.fields).filter(([key]) => !internal.has(key)).map(([key, field]) => {
      const supported = field.status === 'typed' && (selectors.has(key) || key === 'max_trajectories' ||
        ['boolean', 'number', 'integer', 'string'].includes(field.observed_types[0]) || ['filters', 'losses', 'targets', 'aa_bias', 'binder_lengths', 'paratope_conformations', 'binder_shapes', 'validation_models', 'crop_fasta_sequence', 'multitarget_rounds_per_target'].includes(key))
      return <div key={key}><label>{key}{field.has_native_default && <small> Native default: {JSON.stringify(field.native_default)}</small>}
        {supported ? control(key, field) : <span> Unsupported typed control / unresolved source type</span>}</label></div>
    })}
  </section>
}
