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
// ProteinMPNN, FAMPNN and the validators expose only the settings the loop workflow forwards to them. The names
// match the server's allowlist in services/resequence_settings.py; the server also checks every value against the
// engine's own model definition.
export const LOOP_SETTINGS: Record<string, readonly string[]> = {
    mpnn: [
        'seqs_per_design', 'mpnn_temperature', 'mpnn_omitAAs', 'mpnn_checkpoint_type', 'mpnn_checkpoint_model',
        'mpnn_backbone_noise', 'mpnn_num_connections', 'mpnn_output_intermediates', 'mpnn_relax_max_cycles',
        'mpnn_relax_output', 'mpnn_relax_seqs_per_cycle', 'mpnn_relax_convergence_rmsd',
        'mpnn_relax_convergence_score', 'mpnn_relax_convergence_max_cycles'
    ],
    fampnn: [
        'seqs_per_design', 'fampnn_checkpoint', 'fampnn_seed', 'fampnn_presort_by_length', 'fampnn_timestep_mode',
        'fampnn_timestep_start', 'fampnn_timestep_end', 'fampnn_scn_num_steps', 'fampnn_scn_timestep_mode',
        'fampnn_scn_timestep_start', 'fampnn_scn_timestep_end', 'fampnn_scn_step_scale', 'fampnn_scn_s_churn',
        'fampnn_scn_s_noise', 'fampnn_scn_s_t_min', 'fampnn_scn_s_t_max', 'fampnn_temperature', 'fampnn_exclude_cys',
        'fampnn_psce_threshold', 'fampnn_mutation_top_n', 'fampnn_mutation_min_log_odds_delta', 'fampnn_seq_only',
        'fampnn_num_steps', 'fampnn_batch_size', 'fampnn_repack_last'
    ],
    protenix_v2: [
        'protenix_n_sample', 'protenix_n_step', 'protenix_n_cycle', 'protenix_seeds', 'protenix_use_msa'
    ],
    boltz2: [
        'boltz_recycling_steps', 'boltz_sampling_steps', 'boltz_diffusion_samples', 'boltz_max_parallel_samples',
        'boltz_seed', 'boltz_use_potentials', 'boltz_method'
    ],
    esmfold2: [
        'model_variant', 'num_loops', 'num_sampling_steps', 'num_diffusion_samples', 'seed'
    ],
};
const MODEL_ID: Record<string, string> = {
    ligandmpnn: 'ligandmpnn', caliby: 'caliby_experimental', protonpottsmpnn: 'protonpottsmpnn',
    mpnn: 'proteinmpnn', fampnn: 'fampnn', protenix_v2: 'protenix', boltz2: 'boltz2', esmfold2: 'esmfold2',
};
// Validators run with the loop workflow's own fallbacks, which differ from the standalone model defaults.
const LOOP_FALLBACK = new Set(['protenix_v2', 'boltz2', 'esmfold2']);
const modeFor = (engine: string, mode: string) => engine === 'ligandmpnn' ? mode
    : engine === 'caliby' ? 'ensemble_design' : LOOP_FALLBACK.has(engine) ? 'predict' : engine === 'mpnn' || engine === 'fampnn' ? 'design' : 'redesign';
const inputClass = 'w-full rounded border border-slate-600 bg-slate-900 p-2 text-sm';

/** One sparse override per control: untouched means the model owner's default applies. */
function Scalar({ param, value, onChange, prefix = 'Designer setting', showDefault = true }: {
    param: UntypedApiValue; value: unknown; onChange: (value: unknown) => void; prefix?: string; showDefault?: boolean;
}) {
    const aria = `${prefix} ${param.name}`;
    const [text, setText] = useState(value === undefined ? '' : Array.isArray(value) ? value.join(',') : String(value));
    // A validator runs with the loop workflow's own fallbacks, which are not the standalone model's catalog defaults.
    const shown = (v: unknown) => (!showDefault || v === undefined || v === null ? '' : Array.isArray(v) ? v.join(', ') : String(v));
    const defaultLabel = showDefault ? 'Model default' : 'Loop default';
    let control;
    if (param.type === 'boolean') {
        control = <select aria-label={aria} className={inputClass} value={value === undefined ? '' : String(value)}
            onChange={event => onChange(event.target.value === '' ? undefined : event.target.value === 'true')}>
            <option value="">{defaultLabel}{showDefault && typeof param.default === 'boolean' ? ` (${param.default ? 'on' : 'off'})` : ''}</option>
            <option value="true">On</option><option value="false">Off</option></select>;
    } else if (Array.isArray(param.enum) && param.enum.length) {
        control = <select aria-label={aria} className={inputClass} value={value === undefined ? '' : String(value)}
            onChange={event => onChange(event.target.value === '' ? undefined : event.target.value)}>
            <option value="">{defaultLabel}{shown(param.default) ? ` (${shown(param.default)})` : ''}</option>
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
export type DesignerSettingsDraft = { values: Values; valid: boolean; structuredText?: string };
export function DesignerSettingsForm({ engine, mode, initialValues, onChange }: { engine: string; mode: string; initialValues?: DesignerSettingsDraft; onChange: (values: Values, valid: boolean, structuredText?: string) => void }) {
    const modelId = MODEL_ID[engine];
    const modeId = modeFor(engine, mode);
    const isValidator = LOOP_FALLBACK.has(engine);
    const model = useQuery({
        queryKey: ['resequence-designer-model', modelId],
        queryFn: async ({ signal }) => (await fetchModelById(modelId, signal)).data as UntypedApiValue,
        staleTime: Infinity,
    });
    const [values, setValues] = useState<Values>(initialValues?.values ?? {});
    const [structuredText, setStructuredText] = useState(initialValues?.structuredText ?? '');
    const fields: UntypedApiValue[] = useMemo(() => {
        const doc = model.data;
        if (!doc) return [];
        const modeDoc = (Array.isArray(doc.modes) ? doc.modes : []).find((item: UntypedApiValue) => item.id === modeId);
        const allowed = modeDoc?.params?.length ? new Set<string>(modeDoc.params) : null;
        const listed = LOOP_SETTINGS[engine];
        return (doc.params ?? []).filter((param: UntypedApiValue) =>
            !param.hidden && (!allowed || allowed.has(param.name))
            && (listed ? listed.includes(param.name) : !LOOP_OWNED[engine].includes(param.name)));
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
    const structuredKey = JSON.stringify([...structuredNames]);
    const merged = useMemo(() => ({ ...Object.fromEntries(Object.entries(values).filter(([name]) => !structuredNames.has(name))), ...(parsed ?? {}) }), [values, parsed, structuredKey]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => { onChange(merged, parsed !== null, structuredText); }, [merged, parsed, structuredText]); // eslint-disable-line react-hooks/exhaustive-deps
    const set = (name: string, value: unknown) => setValues(previous => {
        const next = { ...previous };
        if (value === undefined) delete next[name]; else next[name] = value;
        return next;
    });
    if (model.isError) return <p role="alert">Could not load this designer's settings from the server.</p>;
    if (!model.data) return <p className="text-xs">Loading designer settings…</p>;
    return <div className="space-y-3" data-designer-settings={engine}>
        <p className="text-xs text-[var(--text-secondary)]">{isValidator
            ? 'Leave a setting on the loop default unless you want to change it. The loop supplies the sequence.'
            : 'Leave a setting on its model default unless you want to change it. Structure and positions come from the selections above.'}</p>
        <SequenceDesignerSettings fields={scalars}
            groupForField={isValidator ? (param => /seed/.test(param.name) ? 'Execution and reproducibility' : 'Prediction settings') : undefined}
            initiallyOpenGroups={isValidator ? ['Prediction settings'] : undefined}
            renderField={param => <Scalar key={`${modeId}:${param.name}`} param={param} value={values[param.name]}
                prefix={isValidator ? `Validator setting ${engine}` : 'Designer setting'} showDefault={!isValidator}
                onChange={value => set(param.name, value)} />} />
        {structured.length > 0 && <details className="rounded border border-[var(--border-primary)] p-3">
            <summary className="cursor-pointer text-sm font-medium">Structured native settings (JSON)</summary>
            <p className="text-xs text-[var(--text-secondary)]">Tables and lists: {structured.map(param => param.name).join(', ')}.</p>
            <textarea aria-label="Structured designer settings" className={inputClass} rows={4} placeholder="{}" value={structuredText}
                onChange={event => setStructuredText(event.target.value)} />
            {parsed === null && <span role="alert">Enter a JSON object using only the names listed above.</span>}
        </details>}
    </div>;
}
