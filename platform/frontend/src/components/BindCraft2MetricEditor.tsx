import { useState } from 'react';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';
import type { BC2Inventory } from './BindCraft2Settings';
import { BindCraft2ListEditor } from './BindCraft2ListEditor';
import { BC2InterfaceMask, BC2Number } from './BindCraft2NativeControls';
import { bc2Label, BC2_METRIC_HELP, bc2MetricUnit, bc2ParamUnit } from '../lib/bindcraft2ControlMetadata';

type Entry = Record<string, unknown>;
export function BindCraft2MetricEditor({ kind, inventory, value, inherited, effective, weights, onWeightChange, onChange }: {
  kind: 'losses' | 'filters'; inventory: BC2Inventory; value: unknown; inherited?: unknown; effective?: unknown; weights?: Record<string, unknown>; onWeightChange?: (key: string, next: number | undefined) => void; onChange: (next: unknown) => void;
}) {
  const [search, setSearch] = useState('');
  const entries = (value ?? {}) as Record<string, Entry>;
  const inheritedEntries = (inherited ?? {}) as Record<string, Entry>;
  const effectiveEntries = (effective ?? {}) as Record<string, Entry>;
  const update = (metric: string, entry: Entry) => onChange({ ...entries, [metric]: entry });
  const registry = inventory.registered_metrics[kind] ?? {};
  const names = Object.keys(registry).filter(name => bc2Label(name).toLowerCase().includes(search.toLowerCase()) || name.toLowerCase().includes(search.toLowerCase()));
  const row = (metric: string) => {
    const info = registry[metric];
    const requested = entries[metric];
    const base = effectiveEntries[metric] ?? inheritedEntries[metric];
    // Per-leaf display precedence only; writes below use requested, never this view.
    const entry: Entry = Object.fromEntries(['threshold', 'higher', 'mandatory', 'prediction_state'].map(key => [key, requested && Object.hasOwn(requested, key) ? requested[key] : base?.[key]]));
    const writeEntry = (name: string, next: unknown) => { const copy = { ...requested }; if (next === undefined) delete copy[name]; else copy[name] = next; update(metric, copy); };
    return <div key={metric} className="min-w-0 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3">
      <div className="flex items-center justify-between gap-3">
        <label className="!flex items-center gap-2 text-sm font-medium"><input type="checkbox" aria-label={`${kind}.${metric}.enabled`} aria-description="Override inherited metric; this checkbox does not enable or disable native activation" checked={requested !== undefined} onChange={event => {
          if (event.currentTarget.checked) {
            // Customizing is deliberate. Filters require an explicit cutoff at
            // the existing receiving owner; copy only that scalar, never params.
            update(metric, kind === 'filters' && base && Object.hasOwn(base, 'threshold') ? { threshold: base.threshold } : {});
          } else { const copy = { ...entries }; delete copy[metric]; onChange(copy); }
        }} />{bc2Label(metric)} · Override inherited metric</label>
        {entry && kind === 'filters' && <span className="text-xs tabular-nums text-[var(--text-secondary)]">{entry.threshold === null ? 'Disabled (null)' : `${entry.higher === true ? '≥ ' : entry.higher === false ? '≤ ' : ''}${entry.threshold === undefined ? 'Set threshold' : String(entry.threshold)}`}</span>}
      </div>
      {kind === 'filters' && requested && !Object.hasOwn(requested, 'threshold') && <p className="text-xs">Explicit cutoff required for a filter override. Inherited cutoff is reference only until supplied; remove the override to restore inheritance.</p>}
      <p className="text-xs">{requested ? 'Customized override · remove to restore inheritance' : base ? 'Inherited native metric' : 'Native activation unavailable'}{entry && kind === 'losses' ? weights?.[`weights_${metric}`] === 0 ? ' · Zero contribution' : typeof weights?.[`weights_${metric}`] === 'number' ? ' · Active objective' : ' · Native activation unavailable' : entry && entry.threshold === null ? ' · Disabled (explicit null)' : entry && entry.threshold !== undefined ? ' · Active filter' : ''}</p>
      {BC2_METRIC_HELP[metric] && <p className="mt-1 text-xs text-[var(--text-secondary)]">{BC2_METRIC_HELP[metric]}</p>}
      <NativeSettingsDisclosure className="mt-2" summary={<span className="cursor-pointer text-xs text-[var(--accent-primary)]">Configure {bc2Label(metric)}</span>}>{() =>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {kind === 'losses' && weights && Object.hasOwn(weights, `weights_${metric}`) && <label>Objective weight<BC2Number label={`${kind}.${metric}.weight`} value={weights[`weights_${metric}`]} onChange={next => onWeightChange?.(`weights_${metric}`, next)} /><small>Weight is a separate native setting; parameters do not activate an objective. Zero turns off its contribution. Reset in Expert settings restores inheritance.</small></label>}
          {kind === 'filters' && <>
            <label>Acceptance threshold<select aria-label={`${kind}.${metric}.threshold.mode`} value={entry.threshold === null ? 'disabled' : 'number'} onChange={event => writeEntry('threshold', event.currentTarget.value === 'disabled' ? null : undefined)}><option value="number">Numeric cutoff</option><option value="disabled">Disable filter (explicit null)</option></select>
              {entry.threshold !== null && <BC2Number label={`${kind}.${metric}.threshold`} unit={bc2MetricUnit(metric)} value={requested && !Object.hasOwn(requested, 'threshold') ? undefined : entry.threshold} onChange={next => writeEntry('threshold', next)} />}
            </label>
            {(['higher', 'mandatory'] as const).map(flag => <label key={flag}>{flag === 'higher' ? 'Passing direction' : 'Mandatory filter'}<select aria-label={`${kind}.${metric}.${flag}`} value={entry[flag] === undefined ? 'native' : String(entry[flag])} onChange={event => writeEntry(flag, event.currentTarget.value === 'native' ? undefined : event.currentTarget.value === 'true')}>
              <option value="native">Native behavior (omitted)</option><option value="true">{flag === 'higher' ? 'At or above threshold' : 'Yes'}</option><option value="false">{flag === 'higher' ? 'At or below threshold' : 'No'}</option>
            </select></label>)}
          </>}
          <label>Prediction state override<input aria-label={`${kind}.${metric}.prediction_state`} value={entry.prediction_state as string ?? ''} placeholder="Native state" onChange={event => writeEntry('prediction_state', event.currentTarget.value || undefined)} /></label>
          {Object.entries(info.params).map(([param, descriptor]) => {
            const label = `${kind}.${metric}.params.${param}`;
            if (!descriptor.request_types) return <p key={param}>{bc2Label(param)}: {descriptor.unresolved_reason ?? 'Native type not available in discovery'}</p>;
            const defaultValue = Object.hasOwn(descriptor, 'resolved_default') ? descriptor.resolved_default : descriptor.default_literal;
            const params = (requested?.params ?? {}) as Record<string, unknown>;
            const inheritedParams = (base?.params ?? {}) as Record<string, unknown>;
            const explicit = Object.hasOwn(params, param);
            const actual = explicit ? params[param] : Object.hasOwn(inheritedParams, param) ? inheritedParams[param] : defaultValue;
            const write = (next: unknown) => { const copy = { ...params }; if (next === undefined) delete copy[param]; else copy[param] = next; update(metric, { ...requested, params: copy }); };
            const nullable = descriptor.request_types.includes('null');
            const numeric = descriptor.request_types.includes('number') || descriptor.request_types.includes('integer');
            return <div key={param} className={param === 'interface_mask' || descriptor.request_types.includes('array') ? 'md:col-span-2 space-y-2' : 'space-y-2'}>
              {param === 'interface_mask' && descriptor.request_types.includes('array') ? <BC2InterfaceMask label={label} value={actual} onChange={write} omitted={!explicit && !Object.hasOwn(inheritedParams, param)} /> : <>
                <label className="space-y-1"><span className="text-sm">{bc2Label(param)}</span>
                  {descriptor.native_default_encoding && <small>Native default: {descriptor.native_default_encoding}. Leave omitted to retain the native sentinel.</small>}
                  {nullable && <select aria-label={`${label}.mode`} value={actual === null ? 'null' : 'value'} onChange={event => write(event.currentTarget.value === 'null' ? null : descriptor.request_types?.includes('array') ? [] : '')}><option value="null">Automatic / native null</option><option value="value">Explicit value</option></select>}
                  {actual === null && nullable ? null : descriptor.request_types.includes('array') ? <BindCraft2ListEditor label={label} value={Array.isArray(actual) ? actual : []} onChange={write} /> : descriptor.request_types.includes('boolean') ? <input type="checkbox" aria-label={label} checked={actual === true} onChange={event => write(event.currentTarget.checked)} /> : numeric ? <BC2Number label={label} unit={bc2ParamUnit(param)} integer={descriptor.request_types.includes('integer')} value={actual} onChange={write} /> : <input aria-label={label} value={actual == null ? '' : String(actual)} onChange={event => write(event.currentTarget.value)} />}
                </label>
              </>}
              {!explicit && <small>Inherited / native signature: {JSON.stringify(actual)}{descriptor.source_default ? ` · ${descriptor.source_default}` : ''}. Editing writes only this parameter.</small>}
              {explicit && <button type="button" className="text-xs" aria-label={`Reset ${label}`} onClick={() => write(undefined)}>Restore inherited / signature value</button>}
            </div>;
          })}
        </div>
      }</NativeSettingsDisclosure>
    </div>;
  };
  const selected = names.filter(name => Object.hasOwn(entries, name) || Object.hasOwn(inheritedEntries, name) || Object.hasOwn(effectiveEntries, name));
  const available = names.filter(name => !selected.includes(name));
  return <div className="space-y-3">
    <p className="text-xs text-[var(--text-secondary)]">{kind === 'losses' ? 'Configure state-specific objectives here; relative weights are in Expert settings → Objective weights.' : 'Set metric-specific acceptance thresholds, direction and mandatory behavior.'} Omitted nested values follow native/profile resolution. Removing an entry removes its override, not a preset’s objective or filter. Set an objective’s weight to zero to turn off its contribution.</p>
    <input type="search" aria-label={`Find ${kind}`} placeholder={kind === 'losses' ? 'Find an objective…' : 'Find an acceptance metric…'} value={search} onChange={event => setSearch(event.currentTarget.value)} />
    <div className="grid gap-2 lg:grid-cols-2">{selected.map(row)}</div>
    <NativeSettingsDisclosure open={search ? true : undefined} summary={<span className="cursor-pointer text-sm text-[var(--accent-primary)]">Add {kind === 'losses' ? 'objectives' : 'acceptance metrics'} · {available.length} available</span>}>{() => <div className="mt-3 grid gap-2 lg:grid-cols-2">{available.map(row)}</div>}</NativeSettingsDisclosure>
    {!names.length && <p className="text-sm">No matching native metrics.</p>}
  </div>;
}
