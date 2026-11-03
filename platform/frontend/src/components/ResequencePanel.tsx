import { useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { DeNovoDraftPreparationContext } from '../lib/deNovoDocumentCapture';
import { submissionExecutionPolicy, type ExecutionPolicy } from '../lib/executionPolicy';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api, submitJob, prepareJobSubmission, completeCurrentLaunchContext, EXECUTION_TARGET_STORAGE_KEY, type Job, type NativeGenerationSourceBinding, type NativeGenerationSourceBindings, type StructureSourceSelection } from '../lib/api';
import { NativeSourceInput } from './DeNovoNativeSources';
import { DesignerSettingsForm, type DesignerSettingsDraft, LOOP_SETTINGS } from './DesignerSettingsForm';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';

const ENGINES = [['mpnn', 'ProteinMPNN'], ['fampnn', 'FAMPNN'], ['ligandmpnn', 'LigandMPNN'], ['caliby', 'Caliby'], ['protonpottsmpnn', 'ProtonPottsMPNN (pH-specific redesign)']] as const;
const LIGAND_MODES = [['ligand_aware', 'Ligand-aware'], ['ntp_aware', 'NTP-aware'], ['metal_aware', 'Metal-aware'], ['dna_aware', 'DNA-aware']] as const;
const VALIDATORS = [['protenix_v2', 'Protenix v2'], ['esmfold2', 'ESMFold2 (optional)'], ['boltz2', 'Boltz-2']] as const;

/** Resequence existing designs from any generator, then validate. Positions are caller-selected. */
export function ResequencePanel({ initialValues = {}, onDraftChange, runDetails }: { initialValues?: Record<string, unknown>; onDraftChange?: (draft: Record<string, unknown>) => void; runDetails?: ReactNode }) {
    const preparation = useContext(DeNovoDraftPreparationContext);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const saved = initialValues as Record<string, UntypedApiValue>;
    const navigate = useNavigate();
    const [bindings, setBindings] = useState<NativeGenerationSourceBindings | undefined>(saved.native_sources);
    const [engine, setEngine] = useState<string>(saved.seq_method ?? 'mpnn');
    const [ligandMode, setLigandMode] = useState(saved.designer_mode ?? (saved.designer_template ? '' : 'ligand_aware'));
    const [designerSettings, setDesignerSettings] = useState<Record<string, DesignerSettingsDraft>>(saved.designer_drafts ?? {});
    const designerKey = `${engine}:${engine === 'ligandmpnn' ? ligandMode : ''}`;
    const designer = designerSettings[designerKey] ?? { values: Object.fromEntries((LOOP_SETTINGS[engine] ?? []).filter(key => Object.hasOwn(saved, key)).map(key => [key, saved[key]])), valid: true };
    const [validatorSettings, setValidatorSettings] = useState<Record<string, DesignerSettingsDraft>>(saved.validator_drafts ?? Object.fromEntries(VALIDATORS.map(([id]) => [id, { values: Object.fromEntries((LOOP_SETTINGS[id] ?? []).filter(key => Object.hasOwn(saved, key)).map(key => [key, saved[key]])), valid: true }])));
    const [chains, setChains] = useState(saved.design_chains ?? 'A');
    const [fixed, setFixed] = useState(saved.fixed_positions ?? '');
    const [designable, setDesignable] = useState(saved.designable_positions ?? '');
    const [validators, setValidators] = useState<string[]>(saved.structure_validators ?? ['protenix_v2']);
    const [name, setName] = useState(saved.job_name ?? saved.name ?? 'Resequence');
    // Each launch picks its own placement: Local, or any ready worker. A source that finished on one worker does not tie the next round to it.
    const [targetId, setTargetId] = useState<string | null>(saved.execution_target_id ?? null);
    const [policy, setPolicy] = useState<ExecutionPolicy>(() => saved.execution_policy ?? submissionExecutionPolicy());
    useEffect(() => { const receive = () => setPolicy(submissionExecutionPolicy()); window.addEventListener('bms:execution-policy-change', receive); return () => window.removeEventListener('bms:execution-policy-change', receive); }, []);
    const [msaProvider, setMsaProvider] = useState<string>(saved.msa_provider ?? saved.protenix_msa_backend ?? '');
    const [error, setError] = useState<string | null>(null);
    const binding = bindings?.input;
    const source: StructureSourceSelection | null = binding && !('kind' in binding) ? binding : null;
    const submit = useMutation({
        mutationFn: async () => {
            if (!targetId && !saved.backbone_input_pdbs && !prepared.current) return (await api.post<Job>('/api/jobs/resequence/from-structures', { ...request, ...placement })).data;
            let staged: typeof prepared.current = null;
            let owned = false;
            try {
                const job = await prepareCurrent.current(false);
                staged = prepared.current; owned = ownedPreparation.current;
                return (await submitJob(job)).data as Job;
            } catch (cause) {
                const receipt = staged?.job.params?.resequence_preparation;
                if (owned && receipt) {
                    // The server capability owner refuses cleanup of Job-owned inputs.
                    // Draft/Project-prepared receipts are not this launch attempt's disposal authority.
                    await api.post('/api/jobs/resequence/from-structures/cleanup', { preparation: receipt });
                    if (prepared.current === staged) {
                        prepared.current = null; ownedPreparation.current = false;
                        if (alive.current) current.current.onDraftChange?.({ ...current.current.draft, resequence_prepared: undefined, native_job_request: null });
                    }
                }
                throw cause;
            }
        },
        onSuccess: async (job) => { prepared.current = null; navigate(await completeCurrentLaunchContext(job) ?? '/'); },
        onError: (cause: Error) => setError(cause.message || 'Failed to start resequencing'),
    });
    const toggle = (id: string) => setValidators(previous => previous.includes(id) ? previous.filter(x => x !== id) : [...previous, id]);
    const ownSettings = ['mpnn', 'fampnn', 'ligandmpnn', 'caliby', 'protonpottsmpnn'].includes(engine);
    // Only selected validators enter the request; inactive editor settings remain in the suite draft.
    const sentValidatorSettings = Object.fromEntries(validators
        .filter(id => Object.keys(validatorSettings[id]?.values ?? {}).length > 0)
        .map(id => [id, validatorSettings[id].values]));
    const validatorsValid = validators.every(id => validatorSettings[id]?.valid !== false);
    const ready = Boolean(source || saved.backbone_input_pdbs) && chains.trim() !== '' && validators.length > 0 && !(fixed.trim() && designable.trim())
        && (!ownSettings || designer.valid) && validatorsValid;
    const placement = prepareJobSubmission({ execution_target_id: targetId, execution_policy: policy });
    const request = {
        sources: source ? [source] : saved.sources ?? [], seq_method: engine,
        ...(engine === 'ligandmpnn' && ligandMode ? { designer_mode: ligandMode } : {}),
        ...(ownSettings && Object.keys(designer.values).length ? { designer_settings: designer.values } : {}),
        design_chains: chains.trim(), name: name.trim() || 'Resequence', structure_validators: validators,
        ...(Object.keys(sentValidatorSettings).length ? { validator_settings: sentValidatorSettings } : {}),
        ...(msaProvider ? { msa_provider: msaProvider } : {}),
        ...(fixed.trim() ? { fixed_positions: fixed.trim() } : {}),
        ...(designable.trim() ? { designable_positions: designable.trim() } : {}),
    };
    const signature = JSON.stringify(request);
    const prepared = useRef<{ signature: string; job: Partial<Job> } | null>(saved.resequence_prepared ?? null);
    const ownedPreparation = useRef(false);
    const draft = {
        ...saved, ...request, job_name: name, design_chains: chains, fixed_positions: fixed, designable_positions: designable,
        native_sources: bindings, designer_mode: ligandMode, msa_provider: msaProvider,
        designer_drafts: designerSettings, validator_drafts: validatorSettings, ...placement,
        resequence_prepared: prepared.current?.signature === signature ? prepared.current : undefined,
        native_job_request: ready && prepared.current?.signature === signature ? { ...prepared.current.job, ...placement } : null,
    };
    const current = useRef({ draft, signature, request, placement, onDraftChange });
    current.current = { draft, signature, request, placement, onDraftChange };
    const draftJson = JSON.stringify(draft);
    useEffect(() => { onDraftChange?.(JSON.parse(draftJson)); }, [draftJson, onDraftChange]);
    const prepareCurrent = useRef<(forDraft?: boolean) => Promise<Partial<Job>>>(async () => ({}));
    prepareCurrent.current = async (forDraft = true) => {
        const snapshot = current.current;
        if (!ready) { snapshot.onDraftChange?.({ ...snapshot.draft, native_job_request: null }); return {}; }
        if (prepared.current?.signature !== snapshot.signature) {
            const job = (await api.post<Partial<Job>>('/api/jobs/resequence/from-structures/prepare', {
                ...snapshot.request,
                ...(saved.backbone_input_pdbs && !source ? { backbone_input_pdbs: saved.backbone_input_pdbs, retained_params: saved.resequence_job_params ?? {} } : {}),
                ...snapshot.placement,
            })).data;
            prepared.current = { signature: snapshot.signature, job };
            ownedPreparation.current = !forDraft;
        }
        if (forDraft) ownedPreparation.current = false;
        const job = { ...prepared.current.job, ...snapshot.placement };
        // Publish only to the same current document; an in-flight capture must not overwrite a newer draft.
        if (alive.current && current.current.signature === snapshot.signature) snapshot.onDraftChange?.({ ...snapshot.draft, resequence_prepared: prepared.current, native_job_request: job });
        return job;
    };
    useEffect(() => {
        if (!preparation) return;
        const prepare = async () => { await prepareCurrent.current(); };
        preparation.current = prepare;
        return () => { if (preparation.current === prepare) preparation.current = null; };
    }, [preparation]);
    return <section className="space-y-3" aria-label="Resequence and validate" data-resequence-panel>
        <p className="text-xs text-[var(--text-secondary)]">Resequence a design from any generator, then validate it. The loop pauses for review when it finishes. ProtonPottsMPNN picks its own sites, so keep/change positions are not applied to it.</p>
        <NativeSourceInput integrated label="Structure to resequence" value={source ? 'input' : ''} bindings={bindings}
            onChange={slot => { if (slot === '') setBindings(undefined); }}
            onAcquire={(next: NativeGenerationSourceBinding) => { setBindings({ input: next }); return 'input'; }} />
        <div className="grid grid-cols-2 gap-3">
            <label>Sequence engine<select aria-label="Resequence engine" value={engine} onChange={event => setEngine(event.target.value)}>
                {ENGINES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Design chain(s)<input aria-label="Resequence design chains" value={chains} onChange={event => setChains(event.target.value)} /></label>
            <label>Keep these residues<input aria-label="Resequence fixed positions" placeholder="A1-20,B5" value={fixed} onChange={event => setFixed(event.target.value)} /></label>
            <label>Or change only these<input aria-label="Resequence designable positions" placeholder="A21-40" value={designable} onChange={event => setDesignable(event.target.value)} /></label>
        </div>
        <p className="text-xs text-[var(--text-secondary)]">Leave both empty to redesign every residue of the design chains. Fill one, not both.</p>
        <fieldset><legend>Validate with</legend>
            {VALIDATORS.map(([id, label]) => <label key={id} className="mr-4"><input type="checkbox" aria-label={`Validate with ${label}`} checked={validators.includes(id)} onChange={() => toggle(id)} /> {label}</label>)}
        </fieldset>
        {VALIDATORS.filter(([id]) => validators.includes(id)).map(([id, label]) =>
            <details key={id} className="rounded border border-[var(--border-primary)] p-3" data-validator-settings={id}>
                <summary className="cursor-pointer text-sm font-medium">{label} settings</summary>
                <DesignerSettingsForm engine={id} mode="" initialValues={validatorSettings[id]} onChange={(values, valid, structuredText) => setValidatorSettings(previous => ({ ...previous, [id]: { values, valid, structuredText } }))} />
            </details>)}
        <label>MSA provider<select aria-label="Resequence MSA provider" value={msaProvider} onChange={event => setMsaProvider(event.target.value)}><option value="">Loop default (ColabFold)</option><option value="colabfold_api">ColabFold</option><option value="neurosnap_api">Neurosnap</option></select></label>
        <label>Job name<input aria-label="Resequence job name" value={name} onChange={event => setName(event.target.value)} /></label>
        <p className="text-xs text-[var(--text-secondary)]">Run this round on this system or on any ready worker, whatever ran the earlier round.</p>
        <ExecutionTargetPicker value={targetId ?? ''} onChange={value => { setTargetId(value); if (value) sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, value); else sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY); window.dispatchEvent(new Event('bms:execution-target-change')); }} disabled={submit.isPending} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
        {runDetails}
        {error && <p role="alert">{error}</p>}
        {engine === 'ligandmpnn' && <label>LigandMPNN mode<select aria-label="LigandMPNN mode" value={ligandMode} onChange={event => setLigandMode(event.target.value)}>
            {saved.designer_template && <option value="">Retained native template mode</option>}
            {LIGAND_MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
        {ownSettings && (engine !== 'ligandmpnn' || ligandMode) && <DesignerSettingsForm key={designerKey} engine={engine} mode={ligandMode} initialValues={designer} onChange={(values, valid, structuredText) => setDesignerSettings(previous => ({ ...previous, [designerKey]: { values, valid, structuredText } }))} />}
        <button type="button" disabled={!ready || submit.isPending} onClick={() => { setError(null); submit.mutate(); }}>Run resequence and validate</button>
    </section>;
}
