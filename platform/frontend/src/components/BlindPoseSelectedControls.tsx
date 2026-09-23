import { useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import {
    fetchBlindPoseSelectedResult, fetchLigandInterfaceContextResult,
    submitBlindPoseSelected, submitLigandInterfaceContext,
    type BlindPoseSelectedRequest, type SelectedNativeResult,
} from '../lib/api';
import BindLigandMPNNInterfaceContext, {
    type BindInterfaceContextSelection, type BindInterfaceContextSettings,
} from './BindLigandMPNNInterfaceContext';

const message = (error: unknown) => isAxiosError(error)
    ? String(error.response?.data?.detail ?? error.message)
    : error instanceof Error ? error.message : String(error);

const initialBlindSettings: BlindPoseSelectedRequest['settings'] = {
    model_variant: 'fast', model_id_or_path: '', num_loops: 3,
    num_sampling_steps: 50, num_diffusion_samples: 1,
};
const initialInterfaceSettings: BindInterfaceContextSettings = {
    binder_chain: '', target_chain: '', target_patch: [], seed: 0, samples: 1, temperature: 0.1,
};

interface Props {
    sourceJobId: string;
    sourceModelId: string;
    sourceParams: Record<string, unknown>;
    selectedDesignIds: string[];
    resultJob?: { id: string; model_id: string; mode: string; status: string; error_message?: string | null };
    onOpenJob: (id: string) => void;
}

/** Independent exploratory actions; source ownership and chain validity are resolved by the API. */
export default function BlindPoseSelectedControls({ sourceJobId, sourceModelId, sourceParams, selectedDesignIds, resultJob, onOpenJob }: Props) {
    const [blindSettings, setBlindSettings] = useState(initialBlindSettings);
    const [targetName, setTargetName] = useState('');
    const [binderChains, setBinderChains] = useState<Record<string, string>>({});
    const [targetChains, setTargetChains] = useState('');
    const [interfaceSettings, setInterfaceSettings] = useState(initialInterfaceSettings);
    const [submitting, setSubmitting] = useState<'blind' | 'interface' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [submittedJobId, setSubmittedJobId] = useState<string | null>(null);
    const [native, setNative] = useState<SelectedNativeResult | null>(null);
    const [reading, setReading] = useState(false);
    const isBlindResult = resultJob?.model_id === 'esmfold2' && resultJob.mode === 'blind_pose';
    const isInterfaceResult = resultJob?.model_id === 'ligandmpnn' && resultJob.mode === 'interface_context';
    const isResult = isBlindResult || isInterfaceResult;
    const resultId = isResult ? resultJob.id : null;
    const status = resultJob?.status;

    useEffect(() => {
        setNative(null);
        setError(null);
        if (!resultId || status !== 'completed') return;
        let current = true;
        setReading(true);
        (isBlindResult ? fetchBlindPoseSelectedResult(resultId) : fetchLigandInterfaceContextResult(resultId))
            .then(result => { if (current) setNative(result); })
            .catch(reason => { if (current) setError(message(reason)); })
            .finally(() => { if (current) setReading(false); });
        return () => { current = false; };
    }, [resultId, status, isBlindResult]);

    const run = async (kind: 'blind' | 'interface', selection?: BindInterfaceContextSelection) => {
        setError(null);
        setSubmittedJobId(null);
        setSubmitting(kind);
        try {
            let jobId: string;
            if (kind === 'blind') {
                const response = await submitBlindPoseSelected({
                    source_job_id: sourceJobId,
                    ...(sourceModelId === 'bindcraft2' && targetName ? { target_name: targetName } : {}),
                    design_ids: [...selectedDesignIds],
                    binder_chains: Object.fromEntries(selectedDesignIds.map(id => [id, binderChains[id]?.split(',').map(x => x.trim()).filter(Boolean) ?? []])),
                    target_chains: targetChains.split(',').map(x => x.trim()).filter(Boolean),
                    settings: { ...blindSettings },
                });
                jobId = response.id;
            } else {
                const response = await submitLigandInterfaceContext(selection!);
                jobId = response.job.id;
            }
            setSubmittedJobId(jobId);
        } catch (reason) {
            setError(message(reason));
        } finally {
            setSubmitting(null);
        }
    };

    if (isResult) return <section aria-label="Selected experimental result" className="mb-4 rounded-lg border border-slate-700 p-4 text-sm">
        <h3 className="font-semibold">{isBlindResult ? 'Blind pose' : 'LigandMPNN interface context'} (experimental)</h3>
        <p>Job {resultJob.id}: {status}. Native observations are unclassified; no binding verdict is implied.</p>
        {resultJob.error_message && <p role="alert">{resultJob.error_message}</p>}
        {reading && <p>Reading native result…</p>}
        {error && <p role="alert">Result readback failed: {error}</p>}
        {native && <div aria-label="Native selected records">
            {native.records.map((record, index) => <details key={index} className="mt-2 rounded border border-slate-700 p-2">
                <summary>{String(record.design_id ?? record.candidate_id ?? record.sample_id ?? `Record ${index + 1}`)} · {String(record.status ?? record.classification ?? 'unclassified')}</summary>
                <pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(record, null, 2)}</pre>
            </details>)}
            <details className="mt-2"><summary>Native receipt and settings</summary><pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(native, null, 2)}</pre></details>
        </div>}
    </section>;

    const targets = sourceModelId === 'bindcraft2'
        ? (sourceParams.bindcraft2_settings as { targets?: Array<{ name?: string }> } | undefined)?.targets ?? [] : [];
    return <section aria-label="Selected experimental actions" className="mb-4 rounded-lg border border-slate-700 p-3 text-sm">
        <p className="text-slate-300">Selected Design subset: {selectedDesignIds.length}. Independent exploratory actions; neither produces a binding verdict.</p>
        <details><summary className="cursor-pointer font-semibold">Blind pose (experimental)</summary>
            <div className="mt-3 flex flex-wrap gap-3">
                {sourceModelId === 'bindcraft2' && <label>Declared target <select aria-label="Declared target" value={targetName} onChange={e => setTargetName(e.target.value)}>
                    <option value="">Select target</option>{targets.map(target => target.name && <option key={target.name} value={target.name}>{target.name}</option>)}
                </select></label>}
                <label>Target chains (comma-separated) <input aria-label="Blind pose target chains" value={targetChains} onChange={e => setTargetChains(e.target.value)} placeholder="A" /></label>
                {selectedDesignIds.map(id => <label key={id}>Binder chains for {id} (comma-separated) <input aria-label={`Binder chains for ${id}`} value={binderChains[id] ?? ''} onChange={e => setBinderChains(current => ({ ...current, [id]: e.target.value }))} placeholder="B" /></label>)}
                <label>Checkpoint family <select aria-label="Blind pose model variant" value={blindSettings.model_variant} onChange={e => setBlindSettings(current => ({ ...current, model_variant: e.target.value as 'fast' | 'full' }))}><option value="fast">Fast</option><option value="full">Full</option></select></label>
                <label>Model ID or path override (blank: family default) <input aria-label="Blind pose model ID or path" value={blindSettings.model_id_or_path} onChange={e => setBlindSettings(current => ({ ...current, model_id_or_path: e.target.value }))} /></label>
                {([['num_loops', 'Inference loops', 1, 12], ['num_sampling_steps', 'Diffusion steps', 1, 1000], ['num_diffusion_samples', 'Diffusion samples', 1, 8]] as const).map(([field, label, min, max]) =>
                    <label key={field}>{label} ({min}–{max}) <input aria-label={label} type="number" min={min} max={max} step={1} value={blindSettings[field]} onChange={e => setBlindSettings(current => ({ ...current, [field]: Number(e.target.value) }))} /></label>)}
                <label>Seed (optional) <input aria-label="Blind pose seed" type="number" min={0} step={1} value={blindSettings.seed ?? ''} onChange={e => setBlindSettings(current => {
                    const { seed: _seed, ...rest } = current;
                    return e.target.value === '' ? rest : { ...rest, seed: Number(e.target.value) };
                })} /></label>
            </div>
            <button type="button" disabled={!selectedDesignIds.length || Boolean(submitting)} onClick={() => void run('blind')}>Run selected blind pose</button>
        </details>
        <details className="mt-2"><summary className="cursor-pointer font-semibold">LigandMPNN interface context (experimental)</summary>
            <BindLigandMPNNInterfaceContext sourceJobId={sourceJobId} roundId={sourceJobId} selectedCandidateIds={selectedDesignIds}
                settings={interfaceSettings} onSettingsChange={setInterfaceSettings} submitting={Boolean(submitting)} onSelect={selection => void run('interface', selection)} />
        </details>
        {submitting && <p role="status">Submitting {submitting} action…</p>}
        {error && <p role="alert">{error}</p>}
        {submittedJobId && <p role="status">Submitted Job {submittedJobId}. <button type="button" onClick={() => onOpenJob(submittedJobId)}>Open result Job</button></p>}
    </section>;
}
