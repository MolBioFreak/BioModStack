import { useEffect, useState, type ReactNode } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import type { BinderSelectedControlsProps } from './BinderSelectedControls';
import type { BinderOperation } from '../lib/binderContinuation';
import { activeInvocationPlan } from '../lib/binderInvocationPlan';
import { BinderInvocationPlan } from './BinderInvocationPlan';
import { BinderMdPreviewReview } from './BinderMdPreviewReview';
import { readBinderCandidateDocuments } from '../lib/binderContinuation';
import { candidateKey, fetchSelectedRounds, readRoundStorage, submitSelectedRound, uniqueCandidates, writeRoundStorage, type CandidateRef, type SelectedRoundStage } from '../lib/binderSelectedRound';
import { ExecutionPolicyControl } from './ExecutionPolicyControl';
import { newJobExecutionPolicy, type ExecutionPolicy } from '../lib/executionPolicy';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { BindCraft2SettingsReadback } from './BindCraft2NativeResults';
import { BinderCandidateSets } from './BinderCandidateSets';
import { api, fetchDesignById, submitJob } from '../lib/api';

export interface NativeRoundEditorProps {
    operation: BinderOperation;
    mode?: string;
    candidateRefs?: CandidateRef[];
    params: Record<string, UntypedApiValue>;
    onChange: (params: Record<string, UntypedApiValue>) => void;
}
interface DraftStage { stage: SelectedRoundStage; enabled: boolean; drafts: Record<string, Record<string, UntypedApiValue>>; taskDrafts?: Record<string, Record<string, UntypedApiValue>> }
interface Draft { stages: DraftStage[]; target: string | null; policy?: ExecutionPolicy }
const labels: Record<string, string> = { fampnn: 'FA-MPNN', proteinmpnn: 'ProteinMPNN', caliby_binder: 'Caliby', protonpottsmpnn: 'ProtonPottsMPNN', binder_refinement: 'PPIFlow', protenix: 'Protenix', boltz2: 'Boltz-2', esmfold2: 'ESMFold2' };
const operations: Record<string, BinderOperation> = { fampnn: 'fampnn', proteinmpnn: 'proteinmpnn', caliby_binder: 'caliby', protonpottsmpnn: 'protonpottsmpnn', binder_refinement: 'refine', protenix: 'predict_protenix', boltz2: 'predict_boltz2', esmfold2: 'predict_esmfold2' };
const names = { sequence_design: 'Sequence design', maturation: 'Maturation', prediction: 'Blind prediction', md: 'Molecular dynamics' };
const control = 'min-w-0 max-w-full whitespace-normal rounded border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm';
const identity = () => crypto.randomUUID();
function newStage(kind: SelectedRoundStage['kind'], previous?: string): DraftStage {
    const common = { id: identity(), input: previous ? { stage_id: previous } : { selected: true as const } };
    return { enabled: true, drafts: {}, stage: kind === 'maturation'
        ? { ...common, kind, model_id: 'binder_refinement', mode: 'refine', params: {} }
        : { ...common, kind, models: [] } };
}
export function BinderNextRound({ sourceJobId, selectedDesignIds, selectedNativeSources = [], candidateDocuments, launchContextId, onOpenJob, onClearSelection, renderEditor }: BinderSelectedControlsProps & { renderEditor: (props: NativeRoundEditorProps) => ReactNode }) {
    const [draft, setDraft] = useState<Draft>(() => readRoundStorage(sourceJobId, 'draft', { stages: [], target: null, policy: newJobExecutionPolicy() }));
    const [extra, setExtra] = useState<CandidateRef[]>(() => readRoundStorage(sourceJobId, 'selection', []));
    useEffect(() => {
        const sync = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail.jobId === sourceJobId) { if (detail.replace) onClearSelection?.(); setExtra(detail.refs); } };
        window.addEventListener('bms:binder-round-selection', sync);
        return () => window.removeEventListener('bms:binder-round-selection', sync);
    }, [sourceJobId, onClearSelection]);
    const [open, setOpen] = useState(false);
    const [editing, setEditing] = useState<{ stage: string; model: string }>();
    const [busy, setBusy] = useState(false), [error, setError] = useState('');
    const [pending, setPending] = useState<{ fingerprint: string; key: string } | null>(() => readRoundStorage(sourceJobId, 'pending', null));
    const [roundId, setRoundId] = useState<string>(() => readRoundStorage(sourceJobId, 'round', ''));
    useEffect(() => writeRoundStorage(sourceJobId, 'draft', draft), [sourceJobId, draft]);
    useEffect(() => writeRoundStorage(sourceJobId, 'selection', extra), [sourceJobId, extra]);
    useEffect(() => writeRoundStorage(sourceJobId, 'round', roundId), [sourceJobId, roundId]);
    const rounds = useQuery({ queryKey: ['binder-selected-rounds', sourceJobId], queryFn: ({ signal }) => fetchSelectedRounds(sourceJobId, signal), retry: false, refetchInterval: 5000 });
    const owners = useQueries({ queries: selectedDesignIds.map(id => ({ queryKey: ['binder-candidate-owner', id], queryFn: () => fetchDesignById(id).then(response => response.data.job_id), staleTime: Infinity })) });
    const documents = candidateDocuments ?? readBinderCandidateDocuments(sourceJobId);
    const selection = uniqueCandidates([
        ...selectedDesignIds.map((design_id, index) => ({ kind: 'design' as const, job_id: owners[index]?.data ?? sourceJobId, design_id, ...(documents[design_id] ? { document: documents[design_id] } : {}) })).filter(ref => !extra.some(exact => exact.kind === 'design' && exact.job_id === ref.job_id && exact.design_id === ref.design_id && exact.document?.artifact_id === ref.document?.artifact_id && exact.document?.target_state === ref.document?.target_state)),
        ...selectedNativeSources.filter(source => !extra.some(ref => ref.kind === 'native_document' && ref.job_id === source.job_id && ref.artifact_id === source.artifact_id)).map(source => ({ kind: 'native_document' as const, job_id: source.job_id, artifact_id: source.artifact_id, ...(source.document?.target_state != null ? { target_state: source.document.target_state } : {}) })), ...extra,
    ]);
    const intent = { operation: 'round' as const, source_job_id: sourceJobId, selection: { candidate_refs: selection }, round_plan: activeInvocationPlan(draft.stages), execution_target_id: draft.target, execution_policy: draft.policy ?? newJobExecutionPolicy(), ...(launchContextId !== undefined ? { launch_context_id: launchContextId } : {}) };
    const changeStage = (id: string, update: (stage: DraftStage) => DraftStage) => setDraft(previous => ({ ...previous, stages: previous.stages.map(item => item.stage.id === id ? update(item) : item) }));
    const addStage = (kind: SelectedRoundStage['kind']) => setDraft(previous => ({ ...previous, stages: [...previous.stages, newStage(kind, previous.stages.filter(item => item.enabled).at(-1)?.stage.id)] }));
    const addProteinRecipe = () => setDraft(previous => {
        const flow = newStage('maturation', previous.stages.filter(item => item.enabled).at(-1)?.stage.id);
        if (flow.stage.kind === 'maturation') flow.stage.params = { ppiflow_task: 'protein_binder_partial', maturation_flow_enabled: true, maturation_redesign_enabled: false };
        const design = newStage('sequence_design', flow.stage.id);
        const prediction = newStage('prediction', design.stage.id);
        return { ...previous, stages: [...previous.stages, flow, design, prediction] };
    });
    const run = async () => {
        setBusy(true); setError('');
        const fingerprint = JSON.stringify(intent);
        const retained = pending?.fingerprint === fingerprint ? pending : { fingerprint, key: identity() };
        setPending(retained); writeRoundStorage(sourceJobId, 'pending', retained);
        try {
            const response = await submitSelectedRound({ ...intent, idempotency_key: retained.key });
            setRoundId(response.round_id);
            const readback = await rounds.refetch();
            if (readback.isError || !readback.data?.some(round => round.round_id === response.round_id)) throw Error('Submission returned; persisted round readback is unavailable. Refresh or retry the same intent.');
            setPending(null); writeRoundStorage(sourceJobId, 'pending', null);
            setOpen(false);
        } catch (reason) { setError(isAxiosError(reason) ? JSON.stringify(reason.response?.data?.detail ?? reason.message) : String(reason)); }
        finally { setBusy(false); }
    };
    const updateEditor = (stageId: string, modelId: string, params: Record<string, UntypedApiValue>) => changeStage(stageId, value => {
        if (value.stage.kind !== 'maturation') return { ...value, stage: { ...value.stage, models: value.stage.models.map(model => model.model_id === modelId ? { ...model, params } : model) } };
        const previousTask = value.stage.params.ppiflow_task;
        const nextTask = params.ppiflow_task;
        if (previousTask && nextTask && previousTask !== nextTask) {
            const taskDrafts: Record<string, Record<string, UntypedApiValue>> = { ...value.taskDrafts, [previousTask]: value.stage.params };
            const shared = Object.fromEntries(Object.entries(value.stage.params).filter(([key]) => ['binder_chains', 'target_chains', 'maturation_repack_enabled', 'maturation_anchors_enabled', 'maturation_flow_enabled', 'maturation_redesign_enabled'].includes(key)));
            return { ...value, taskDrafts, stage: { ...value.stage, params: { ...shared, ...taskDrafts[nextTask], ppiflow_task: nextTask } } };
        }
        return { ...value, stage: { ...value.stage, params } };
    });
    const activeEditor = draft.stages.find(item => item.stage.id === editing?.stage);
    const editorParams = activeEditor?.stage.kind === 'maturation' ? activeEditor.stage.params : activeEditor?.stage.models.find(model => model.model_id === editing?.model)?.params;
    const round = rounds.data?.find(item => item.round_id === roundId) ?? rounds.data?.at(-1);
    const toggleOutput = (ref: CandidateRef, checked: boolean) => {
        const refs = checked ? uniqueCandidates([...extra, ref]) : extra.filter(item => candidateKey(item) !== candidateKey(ref));
        setExtra(refs); writeRoundStorage(sourceJobId, 'selection', refs);
        window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId: sourceJobId, refs } }));
    };
    const retry = async () => {
        if (!round) return;
        setBusy(true); setError('');
        try { await api.post(`/api/binder-continuation/${encodeURIComponent(sourceJobId)}/round/retry`, {}, { params: { round_id: round.round_id } }); await rounds.refetch(); }
        catch (reason) { setError(String(reason)); } finally { setBusy(false); }
    };
    return <section aria-label="Selected round workspace" className="bms-binder-controls min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-3"><button className={control} type="button" onClick={() => setOpen(value => !value)}>Next round ({selection.length})</button><span>{selection.length} selected across this workflow</span></div>
        <BinderCandidateSets jobId={sourceJobId} candidates={selection} onLoad={refs => { onClearSelection?.(); setExtra(refs); writeRoundStorage(sourceJobId, 'selection', refs); window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId: sourceJobId, refs, replace: true } })); }} />
        {!!extra.length && <details><summary>Additional selected outputs and saved members ({extra.length})</summary>{extra.map((ref, index) => <div key={candidateKey(ref)}>Candidate {index + 1} · {ref.kind.replaceAll('_', ' ')} <button type="button" onClick={() => toggleOutput(ref, false)}>Remove</button><details><summary>Details</summary><BindCraft2SettingsReadback value={ref} /></details></div>)}</details>}
        {open && <div className="space-y-3 rounded border border-[var(--border-color)] p-4" aria-label="Next round composer">
            <h3 className="font-semibold">Next round</h3>
            <p>Choose stages in order. Models within a stage branch over the same inputs. Inactive drafts are retained, not submitted.</p>
            <p>Protein maturation recipe: flow → chosen designer(s) → blind predictor(s). Flow-only backbones remain available for later design.</p>
            <button type="button" className={control} onClick={addProteinRecipe}>Add protein flow → design → predict</button>
            <div className="flex flex-wrap gap-2">{(['maturation', 'sequence_design', 'prediction', 'md'] as const).map(kind => <button type="button" className={control} key={kind} onClick={() => addStage(kind)}>Add {names[kind]}</button>)}</div>
            <BinderInvocationPlan renderSettings={(call, candidateRefs, onChange) => operations[call.model_id] ? renderEditor({ operation: operations[call.model_id], mode: call.mode, candidateRefs, params: call.params, onChange }) : undefined} showAdd={false} stages={draft.stages} candidates={selection} onChange={stages => setDraft(previous => ({ ...previous, stages }))} />
            {draft.stages.filter(item => item.stage.kind === 'maturation').map(item => <button key={item.stage.id} type="button" onClick={() => setEditing({ stage: item.stage.id, model: 'binder_refinement' })}>PPIFlow settings</button>)}
            {editing && activeEditor && editorParams && <section key={`${editing.stage}:${editing.model}`} aria-label="Stage native editor"><h4>{names[activeEditor.stage.kind]} · {labels[editing.model]}</h4>{'stage_id' in activeEditor.stage.input && <p>Inputs and declared chain roles come from the earlier stage’s actual outputs. Source-stage chain letters are not copied. Explicit role overrides remain editable.</p>}<button type="button" className={control} onClick={() => setEditing(undefined)}>Back to round</button>{renderEditor({ operation: operations[editing.model], mode: activeEditor.stage.kind === 'maturation' ? activeEditor.stage.mode : activeEditor.stage.models.find(call => call.model_id === editing.model)?.mode, candidateRefs: 'selected' in activeEditor.stage.input ? selection : [], params: editorParams, onChange: params => updateEditor(editing.stage, editing.model, params) })}</section>}
            <ExecutionTargetPicker value={draft.target} onChange={target => setDraft(previous => ({ ...previous, target }))} disabled={busy} />
            <ExecutionPolicyControl initialPolicy={draft.policy ?? newJobExecutionPolicy()} onChange={policy => setDraft(previous => ({ ...previous, policy }))} />
            <details><summary>Review exact request</summary><BindCraft2SettingsReadback value={intent} /></details>
            <button type="button" className={control} disabled={busy} onClick={() => void run()}>{busy ? 'Submitting…' : 'Queue round'}</button>
        </div>}
        {error && <p role="alert">{error}</p>}
        {rounds.isError && <p role="status">Round readback unavailable. <button type="button" onClick={() => void rounds.refetch()}>Refresh rounds</button></p>}
        {!!rounds.data?.length && <section aria-label="Returned rounds" className="space-y-2"><label>Round<select aria-label="Returned round" className={control} value={round?.round_id ?? ''} onChange={event => setRoundId(event.target.value)}>{rounds.data.map((value, index) => <option key={value.round_id} value={value.round_id}>Round {index + 2} · {value.state}</option>)}</select></label>
            <span>{round?.state}</span>
            {round?.steps.map(step => <section key={step.step_id} className="rounded border border-[var(--border-color)] p-2"><h4>{labels[step.model_id] ?? step.model_id} · {step.state} · {step.outputs?.length ?? 0} returned</h4>
                {step.job_id && <button type="button" className={control} onClick={() => onOpenJob(step.job_id!)}>Open results</button>}
                {step.error && <p role="status">{step.error}</p>}
                {step.state === 'review_required' && step.review?.code === 'md_native_preview_approval_required' && <BinderMdPreviewReview rootId={round!.root_job_id} roundId={round!.round_id} stepId={step.step_id} digest={step.review.preview_digest} preview={step.md_preview} launchRequest={step.md_launch_request} onApproved={() => rounds.refetch()} />}
                {step.state === 'review_required' && step.review?.code !== 'md_native_preview_approval_required' && step.request && <button type="button" className={control} disabled={busy} onClick={() => { setBusy(true); void submitJob(step.request!, { launchContext: Boolean(step.request?.launch_context_id) }).then(() => rounds.refetch()).catch(reason => setError(String(reason))).finally(() => setBusy(false)); }}>Review remote step</button>}
                {step.outputs?.map((output, index) => <div key={candidateKey(output)}><label><input type="checkbox" aria-label={`Select ${labels[step.model_id] ?? step.model_id} output ${index + 1}`} checked={selection.some(ref => candidateKey(ref) === candidateKey(output))} onChange={event => toggleOutput(output, event.target.checked)} />{output.label ?? output.name ?? `Candidate ${index + 1}`} · {output.kind.replaceAll('_', ' ')}</label><details><summary>Details</summary><BindCraft2SettingsReadback value={output} /></details></div>)}
            </section>)}
            {round && ['needs_retry', 'completed_with_errors'].includes(round.state) && <button type="button" className={control} disabled={busy} onClick={() => void retry()}>Retry round science</button>}
            <details><summary>Round provenance</summary><BindCraft2SettingsReadback value={round} /></details>
        </section>}
    </section>;
}
