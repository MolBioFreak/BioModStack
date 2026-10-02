import React, { useState } from 'react';
import { cohortPhases, describeCohortMetric, splitCohortMetric, type CohortMetricParts } from '../lib/cohortMetricPresentation';

export interface CohortMetricPickerProps { label: string; keys: string[]; value: string; onChange: (key: string) => void; allowEmpty?: boolean }
const control = 'rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-1.5 text-[var(--text-primary)] min-w-0';
const family = (p: CohortMetricParts) => JSON.stringify([p.kind, p.measurement]);
const same = (a: CohortMetricParts, b: CohortMetricParts) => family(a) === family(b) && a.target === b.target && a.phase === b.phase && a.reading === b.reading;
const unique = (values: string[]) => [...new Set(values)];

/** One measurement list, with orthogonal context controls. Never falls back to a different metric. */
export function CohortMetricPicker({ label, keys, value, onChange, allowEmpty = false }: CohortMetricPickerProps) {
    const id = React.useId();
    const [query, setQuery] = useState('');
    const [page, setPage] = useState(0);
    const [draft, setDraft] = useState<{ source: string; parts: CohortMetricParts } | null>(null);
    const current = draft?.source === value ? draft.parts : splitCohortMetric(value);
    const entries = unique(keys).map(key => ({ key, parts: splitCohortMetric(key), info: describeCohortMetric(key, keys) }));
    const families = [...new Map(entries.map(entry => [family(entry.parts), entry])).values()];
    const matches = families.filter(entry => entries.some(e => family(e.parts) === family(entry.parts) && `${e.info.shortLabel} ${e.info.group} ${e.key}`.toLowerCase().includes(query.toLowerCase())));
    const safePage = Math.min(page, Math.max(0, Math.ceil(matches.length / 12) - 1));
    const visible = matches.slice(safePage * 12, safePage * 12 + 12);
    const selectedFamily = families.find(e => family(e.parts) === family(current));
    const options = selectedFamily && !visible.includes(selectedFamily) ? [selectedFamily, ...visible] : visible;
    const relatives = entries.filter(e => family(e.parts) === family(current));
    const resolved = entries.find(e => same(e.parts, current));
    const update = (parts: CohortMetricParts) => {
        const match = entries.find(e => same(e.parts, parts));
        setDraft({ source: match?.key ?? value, parts });
        if (match) onChange(match.key);
    };
    const context = (field: 'target' | 'phase' | 'reading', title: string, values: string[]) => values.length > 0 && <label className="flex min-w-0 flex-col gap-1">{title}
        <select aria-label={`${label} ${title.toLowerCase()}`} className={control} value={current[field]} onChange={e => update({ ...current, [field]: e.target.value })}>
            {!values.includes(current[field]) && <option value={current[field]}>{current[field] || `Choose ${title.toLowerCase()}`}</option>}
            {values.map(v => <option key={v} value={v}>{v === 'last recorded' ? 'Last recorded' : v === 'peak recorded' ? 'Peak recorded (maximum)' : v || 'No target'}</option>)}
        </select>
    </label>;
    return <fieldset className="min-w-0 rounded border border-[var(--border-color)] p-2 text-xs text-[var(--text-primary)]" aria-describedby={`${id}-help`}>
        <legend className="px-1 font-medium">{label}</legend>
        <label className="flex flex-col gap-1">Find measurement<input type="search" aria-label={`${label} search`} className={control} value={query} placeholder="Search measurements or native keys" onChange={e => { setQuery(e.target.value); setPage(0); }} /></label>
        <label className="mt-2 flex flex-col gap-1">Measurement<select aria-label={label} className={control} value={value || draft ? family(current) : ''} onChange={e => {
            if (!e.target.value) { setDraft(null); onChange(''); return; }
            const entry = families.find(f => family(f.parts) === e.target.value);
            if (!entry) return;
            const p = entry.parts;
            // A new trajectory measurement keeps explicit context; incomplete combinations stay pending.
            update(p.kind === current.kind ? { ...current, measurement: p.measurement } : { ...p, target: unique(entries.filter(e => family(e.parts) === family(p)).map(e => e.parts.target)).length === 1 ? p.target : '', phase: p.kind === 'trajectory' ? '' : p.phase, reading: p.kind === 'trajectory' ? '' : p.reading });
        }}>
            <option value="" disabled={!allowEmpty}>{allowEmpty ? 'No color / none' : 'Choose measurement'}</option>
            {!selectedFamily && value && <option value={family(current)} disabled>Selected measurement unavailable</option>}
            {unique(options.map(e => e.info.group)).map(group => <optgroup key={group} label={group}>{options.filter(e => e.info.group === group).map(e => <option key={family(e.parts)} value={family(e.parts)}>{e.info.shortLabel}{families.some(other => family(other.parts) !== family(e.parts) && other.info.shortLabel === e.info.shortLabel) ? ` (${e.parts.kind}; ${e.parts.measurement})` : ''}</option>)}</optgroup>)}
        </select></label>
        {matches.length > 12 && <nav aria-label={`${label} measurement pages`} className="mt-1 flex items-center justify-between gap-2"><button type="button" className={control} disabled={!safePage} onClick={() => setPage(safePage - 1)}>Previous</button><span>{safePage * 12 + 1}–{Math.min(matches.length, (safePage + 1) * 12)} of {matches.length}</span><button type="button" className={control} disabled={(safePage + 1) * 12 >= matches.length} onClick={() => setPage(safePage + 1)}>Next</button></nav>}
        {!matches.length && <p role="status">No matching measurements.</p>}
        <div className="mt-2 grid grid-cols-2 gap-2">
            {context('phase', 'Stage', cohortPhases.filter(p => relatives.some(e => e.parts.phase === p)))}
            {context('reading', 'Reading', unique(relatives.map(e => e.parts.reading).filter(Boolean)))}
            {(current.target || relatives.some(e => e.parts.target)) && context('target', 'Target', unique(relatives.map(e => e.parts.target)))}
        </div>
        <div id={`${id}-help`} className="mt-2 text-[var(--text-secondary)]">
            {resolved ? <details><summary className="cursor-pointer">Measurement help & native key</summary><p className="break-words">{resolved.info.description}</p></details> : value || draft ? <p role="status">This combination is not available. Choose its stage, reading or target. Existing chart selection is unchanged.{value && <span className="block break-words">Current: {describeCohortMetric(value).label}<code className="block">{value}</code></span>}</p> : <span>{allowEmpty ? 'No measurement selected.' : 'Choose a measurement to begin.'}</span>}
        </div>
    </fieldset>;
}
