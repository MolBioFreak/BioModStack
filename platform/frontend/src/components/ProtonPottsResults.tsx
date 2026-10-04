import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { NativeBinderGenerationResults, type NativeWorkbenchAdapter } from './NativeBinderGenerationResults';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';
import { ParamField } from './ModelParameterField';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { StructuralSourceFiles } from './StructuralSourceFiles';
import { SequenceManager } from './SequenceManager';
import { fetchModelById, type Job } from '../lib/api';
import { fetchProtonPottsResults, predictProtonPottsSelected, type PHDesignOutput } from '../lib/protonPottsResults';
import type { NativeGenerationPage, NativeGenerationRecord } from '../lib/nativeBinderResults';
import { readBinderSelection } from '../lib/binderContinuation';
const CohortPlot = lazy(() => import('./CohortAnalytics').then(m => ({ default: m.CohortPlot })));

function NativeDesign({ value }: { value: PHDesignOutput }) {
    return <section aria-label="Native protonation design"><p>Canonical binder sequence</p><code>{value.canonical_sequence}</code><p>Protonation tokens</p><code>{value.extended_tokens.join(' ')}</code>
        <BindCraft2SettingsReadback value={value} />
        {value.energy_trajectory?.length ? <details><summary>Native optimization trajectory</summary><Suspense fallback={<p>Loading native trajectory chart…</p>}><CohortPlot label="Native optimization energies" data={(['selective_energy', 'global_protonation_dH', 'potts_energy'] as const).map(key => {
            const points = value.energy_trajectory!.filter(point => typeof point[key] === 'number' && Number.isFinite(point[key]));
            return { type: 'scatter', mode: 'lines+markers', name: key, x: points.map(point => point.step), y: points.map(point => point[key] as number) };
        })} layout={{ xaxis: { title: { text: 'Native step' } }, yaxis: { title: { text: 'Native energy (uncalibrated)' } } }} /></Suspense><BindCraft2SettingsReadback value={value.energy_trajectory} /></details> : <p>Trajectory not reported.</p>}
    </section>;
}
const systemInputs = new Set(['input_pdb', 'pdb_paths', 'target_pdb', 'sequence', 'sequence_name', 'source_identity_json', 'selected_input_dir', 'selected_input_manifest']);
export default function ProtonPottsResultsView({ job, launchContextId, onOpenJob }: { job: Pick<Job, 'id' | 'status'> & Partial<Pick<Job, 'params'>>; launchContextId?: string | null; onOpenJob?: (id: string) => void }) {
    const query = useQuery({ queryKey: ['protonpottsmpnn-results', job.id], queryFn: ({ signal }) => fetchProtonPottsResults(job.id, signal), retry: false, refetchInterval: ['queued', 'running'].includes(job.status) ? 2000 : false });
    const [selected, setSelected] = useState<string[]>(() => readBinderSelection(`${job.id}:records:protonpottsmpnn`));
    const [predictor, setPredictor] = useState<'protenix' | 'boltz2'>('protenix');
    const [drafts, setDrafts] = useState<Record<string, Record<string, unknown>>>({});
    const [target, setTarget] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string>();
    const [children, setChildren] = useState<Job[]>([]);
    const [fileField, setFileField] = useState<string | null>(null);
    const [sequenceField, setSequenceField] = useState('');
    const [showSequences, setShowSequences] = useState(false);
    const modelQuery = useQuery({ queryKey: ['model', predictor], queryFn: () => fetchModelById(predictor).then(response => response.data) });
    const model = modelQuery.data;
    const fields = useMemo(() => {
        const mode = model?.modes?.find((m: UntypedApiValue) => m.id === 'complex');
        return (model?.params ?? []).filter((p: UntypedApiValue) => !p.hidden && !systemInputs.has(p.name) && (!mode?.params?.length || mode.params.includes(p.name)));
    }, [model]);
    useEffect(() => { if (model) setDrafts(previous => ({ ...previous, [predictor]: { ...Object.fromEntries(fields.filter((p: UntypedApiValue) => p.default !== undefined).map((p: UntypedApiValue) => [p.name, p.default])), ...previous[predictor] } })); }, [model, predictor, fields]);
    const params = drafts[predictor] ?? {};
    const updateParam = (key: string, value: unknown) => setDrafts(previous => ({ ...previous, [predictor]: { ...previous[predictor], [key]: value } }));
    const adapter = useMemo<NativeWorkbenchAdapter | undefined>(() => {
        const data = query.data;
        if (!data) return undefined;
        const records: NativeGenerationRecord[] = data.designs.map(row => ({ candidate_key: row.design_id, native_record: row, native: row.native, metrics: { ...row.native, criteria_index: row.criteria_index }, structures: [] }));
        return { key: 'protonpottsmpnn', title: 'ProtonPottsMPNN native redesign results', recordLabel: 'native designs', label: row => String(row.candidate_key),
            preferredColumns: ['canonical_sequence', 'extended_tokens', 'center_res_ids', 'center_protonation_types', 'final_potts_energy', 'selective_energy', 'global_protonation_dH', 'criteria_index'],
            initialMetrics: () => ({ x: 'final_potts_energy', y: 'selective_energy', distribution: 'global_protonation_dH' }),
            fetchPage: async offset => ({ receipt: { request: data.request, runtime: data.runtime }, publication: { contract: data.contract, source: data.source }, records: records.slice(offset, offset + 100), total: records.length, offset, limit: 100, artifacts: data.artifacts.map(path => ({ path })) } satisfies NativeGenerationPage),
            inspect: row => <NativeDesign value={row.native as PHDesignOutput} />,
        };
    }, [query.data]);
    const run = async () => {
        setBusy(true); setError(undefined);
        try { const response = await predictProtonPottsSelected(job.id, { design_ids: [...selected], model_id: predictor, params: { ...params }, execution_target_id: target, ...(launchContextId ? { launch_context_id: launchContextId } : {}) }); setChildren(response.launched_jobs); }
        catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
        finally { setBusy(false); }
    };
    return <section aria-label="ProtonPottsMPNN results">
        <details><summary>Saved ProtonPottsMPNN scientific request</summary><BindCraft2SettingsReadback value={job.params ?? query.data?.request} /></details>
        {query.isPending && <p role="status">Loading native redesign results…</p>}{query.isError && <p role="alert">{String(query.error)} <button type="button" onClick={() => void query.refetch()}>Retry native redesign results</button></p>}
        {adapter && <NativeBinderGenerationResults jobId={job.id} status={job.status} adapter={adapter} selectedRecordIds={selected} onSelectedRecordIdsChange={setSelected} />}
        <details><summary>Predict selected redesigned sequences</summary><p>Optional prediction uses canonical redesigned binder sequences and retained fixed target sequences. No generated structure is substituted; protonation tokens remain in native results.</p>
            <p>Destination: {launchContextId ? `Project launch context ${launchContextId}` : 'standalone'}.</p>
            <label>Prediction model<select aria-label="Redesign prediction model" value={predictor} onChange={event => setPredictor(event.target.value as typeof predictor)}><option value="protenix">Protenix</option><option value="boltz2">Boltz-2</option></select></label>
            {modelQuery.isPending && <p>Loading prediction settings…</p>}{modelQuery.isError && <p role="alert">{String(modelQuery.error)}</p>}
            <SequenceDesignerSettings fields={fields} renderField={param => <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setFileField} setActiveSequenceField={setSequenceField} setShowSequenceManager={setShowSequences} ligandPresets={[]} />} />
            {fileField && <section aria-label="Prediction input file browser"><StructuralSourceFiles allowSequence onSelect={source => { updateParam(fileField, source.path); setFileField(null); }} /><button type="button" onClick={() => setFileField(null)}>Close prediction file browser</button></section>}
            {showSequences && <SequenceManager onClose={() => setShowSequences(false)} onSelect={sequence => { updateParam(sequenceField, sequence.sequence); setShowSequences(false); }} />}
            <ExecutionTargetPicker value={target} onChange={setTarget} disabled={busy} />
            <BindCraft2SettingsReadback value={{ design_ids: selected, model_id: predictor, params, execution_target_id: target, launch_context_id: launchContextId }} />
            <button type="button" disabled={busy || !selected.length || !model} onClick={() => void run()}>Predict selected redesigned sequences</button>
            {error && <p role="alert">{error}</p>}{children.map(child => <p key={child.id}>{child.name} · {child.status} {onOpenJob ? <button type="button" onClick={() => onOpenJob(child.id)}>Open prediction Job</button> : <a href={`/designs/${encodeURIComponent(child.id)}`}>Open prediction Job</a>}</p>)}
        </details>
    </section>;
}
