import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchModelById } from '../lib/api';
import { nativeParameterLabel } from '../lib/nativeParameterLabel';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';

type Values = Record<string, unknown>;

// Fields the loop fills from its own source and position selection.
const LOOP_OWNED: Record<string, readonly string[]> = {
    ligandmpnn: ['target_pdb', 'ligand_pdb', 'fixed_residues', 'designed_residues', 'fixed_chains', 'designed_chains'],
    caliby: ['ensembles', 'structures'],
    protonpottsmpnn: ['target_pdb', 'binder_chain'],
};
const MODEL_ID: Record<string, string> = { ligandmpnn: 'ligandmpnn', caliby: 'caliby_experimental', protonpottsmpnn: 'protonpottsmpnn' };
const inputClass = 'w-full rounded border border-slate-600 bg-slate-900 p-2 text-sm';

/** One sparse override per control: untouched means the model owner's default applies. */
function Scalar({ param, value, onChange }: { param: UntypedApiValue; value: unknown; onChange: (value: unknown) => void }) {
    const aria = `Designer setting ${param.name}`;
    const [text, setText] = useState(value === undefined ? '' : Array.isArray(value) ? value.join(',') : String(value));
    const shown = (v: unknown) => (v === undefined || v === null ? '' : Array.isArray(v) ? v.join(', ') : String(v));
    let control;
    if (param.type === 'boolean') {
        control = <select aria-label={aria} className={inputClass} value={value === undefined ? '' : String(value)}
            onChange={event => onChange(event.target.value === '' ? undefined : event.target.value === 'true')}>
            <option value="">Model default{typeof param.default === 'boolean' ? ` (${param.default ? 'on' : 'off'})` : ''}</option>
            <option value="true">On</option><option value="false">Off</option></select>;
    } else if (Array.isArray(param.enum) && param.enum.length) {
        control = <select aria-label={aria} className={inputClass} value={value === undefined ? '' : String(value)}
            onChange={event => onChange(event.target.value === '' ? undefined : event.target.value)}>
            <option value="">Model default{shown(param.default) ? ` (${shown(param.default)})` : ''}</option>
            {param.enum.map((option: string) => <option key={option} value={option}>{option}</option>)}</select>;
    } else if (param.type === 'integer' || param.type === 'number') {
        control = <input aria-label={aria} className={inputClass} type="number" step={param.type === 'integer' ? 1 : 'any'}
            min={param.minimum ?? undefined} max={param.maximum ?? undefined} placeholder={shown(param.default)} value={text}
            onChange={event => {
                setText(event.target.value);
                const parsed = param.type === 'integer' ? Number.parseInt(event.target.value, 10) : Number.parseFloat(event.target.value);
                onChange(Number.isFinite(parsed) ? parsed : undefined);
            }} />;
    } else if (param.type === 'string_list') {
        control = <div className="flex gap-2"><input aria-label={aria} className={inputClass} placeholder={shown(param.default)} value={text}
            onChange={event => {
                setText(event.target.value);
                onChange(event.target.value.split(',').map(item => item.trim()).filter(Boolean));
            }} />
            {value !== undefined && <button type="button" aria-label={`Reset ${param.name} to model default`} onClick={() => { setText(''); onChange(undefined); }}>Default</button>}</div>;
    } else {
        control = <input aria-label={aria} className={inputClass} placeholder={shown(param.default)} value={text}
            onChange={event => { setText(event.target.value); onChange(event.target.value === '' ? undefined : event.target.value); }} />;
    }
    return <label className="block text-sm">{nativeParameterLabel(param)}
        {param.description && <span className="block text-xs text-[var(--text-secondary)]">{String(param.description).slice(0, 160)}</span>}
        {control}</label>;
}

/** Settings for a loop designer, built from that designer's own served parameter inventory. */
export function DesignerSettingsForm({ engine, mode, onChange }: { engine: string; mode: string; onChange: (values: Values, valid: boolean) => void }) {
    const modelId = MODEL_ID[engine];
    const modeId = engine === 'ligandmpnn' ? mode : engine === 'caliby' ? 'ensemble_design' : 'redesign';
    const model = useQuery({
        queryKey: ['resequence-designer-model', modelId],
        queryFn: async ({ signal }) => (await fetchModelById(modelId, signal)).data as UntypedApiValue,
        staleTime: Infinity,
    });
    const [values, setValues] = useState<Values>({});
    const [structuredText, setStructuredText] = useState('');
    const fields: UntypedApiValue[] = useMemo(() => {
        const doc = model.data;
        if (!doc) return [];
        const modeDoc = (Array.isArray(doc.modes) ? doc.modes : []).find((item: UntypedApiValue) => item.id === modeId);
        const allowed = modeDoc?.params?.length ? new Set<string>(modeDoc.params) : null;
        return (doc.params ?? []).filter((param: UntypedApiValue) =>
            !param.hidden && (!allowed || allowed.has(param.name)) && !LOOP_OWNED[engine].includes(param.name));
    }, [model.data, modeId, engine]);
    const scalars = fields.filter(param => param.type !== 'object' && param.type !== 'array');
    const structured = fields.filter(param => param.type === 'object' || param.type === 'array');
    const structuredNames = new Set<string>(structured.map(param => param.name));
    const parsed = useMemo((): Values | null => {
        if (!structuredText.trim()) return {};
        try {
            const value = JSON.parse(structuredText);
            if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
            return Object.keys(value).every(key => structuredNames.has(key)) ? value : null;
        } catch { return null; }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [structuredText, structured.length]);
    const merged = useMemo(() => ({ ...values, ...(parsed ?? {}) }), [values, parsed]);
    useEffect(() => { onChange(merged, parsed !== null); }, [merged, parsed]); // eslint-disable-line react-hooks/exhaustive-deps
    const set = (name: string, value: unknown) => setValues(previous => {
        const next = { ...previous };
        if (value === undefined) delete next[name]; else next[name] = value;
        return next;
    });
    if (model.isError) return <p role="alert">Could not load this designer's settings from the server.</p>;
    if (!model.data) return <p className="text-xs">Loading designer settings…</p>;
    return <div className="space-y-3" data-designer-settings={engine}>
        <p className="text-xs text-[var(--text-secondary)]">Leave a setting on its model default unless you want to change it. Structure and positions come from the selections above.</p>
        <SequenceDesignerSettings fields={scalars}
            renderField={param => <Scalar key={`${modeId}:${param.name}`} param={param} value={values[param.name]} onChange={value => set(param.name, value)} />} />
        {structured.length > 0 && <details className="rounded border border-[var(--border-primary)] p-3">
            <summary className="cursor-pointer text-sm font-medium">Structured native settings (JSON)</summary>
            <p className="text-xs text-[var(--text-secondary)]">Tables and lists: {structured.map(param => param.name).join(', ')}.</p>
            <textarea aria-label="Structured designer settings" className={inputClass} rows={4} placeholder="{}" value={structuredText}
                onChange={event => setStructuredText(event.target.value)} />
            {parsed === null && <span role="alert">Enter a JSON object using only the names listed above.</span>}
        </details>}
    </div>;
}
