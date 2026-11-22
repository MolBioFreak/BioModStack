import { useRef, useState } from 'react';
import { bioXpErrorText, type BioXpRecoveryAction } from '../lib/bioxpClient';
import { methodsPost, type MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';


const labels = { retry: 'Retry failed action', repeat: 'Repeat completed action', skip: 'Skip failed action', manual_complete: 'Mark completed by operator' };
type Preview = BioXpRecoveryAction & { job_id: string; submitted: boolean; projected_target?: unknown; affected_actions?: {action_id: string; kind: string}[]; previous_settings: MethodValue; proposed_settings: MethodValue; editable_settings: string[]; attempt_results: unknown[]; sibling_results: unknown[] };

/** Report and preview are read-only. Submission stays with the original job monitor. */
export function BioXpRecoveryDecision({ jobId, generation, gateId, report, disabled, send }: {
    jobId: string; generation: number; gateId: string; report?: MethodValue | null; disabled: boolean;
    send: (action: BioXpRecoveryAction) => void;
}) {
    const latest = new Map<string, MethodValue>();
    for (const raw of Array.isArray(report?.action_results) ? report.action_results : []) {
        const row = object(raw);
        if (typeof row.action_id === 'string' && typeof row.source_occurrence_id === 'string' && typeof row.attempt === 'number' && Number.isSafeInteger(row.attempt) && row.attempt > 0)
            latest.set(row.action_id, row);
    }
    const [scope, setScope] = useState<'action' | 'occurrence'>('action');
    const [member, setMember] = useState('');
    const [target, setTarget] = useState('');
    const [decision, setDecision] = useState<BioXpRecoveryAction['decision'] | ''>('');
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [preview, setPreview] = useState<Preview | null>(null);
    const [error, setError] = useState('');
    const revision = useRef(0);
    const reset = () => { revision.current++; setPreview(null); setDraft({}); setError(''); };
    const row = latest.get(target);
    const provenance = object(object(report?.method_snapshot).compilation).provenance;
    const members = (Array.isArray(provenance) ? provenance.map(object) : []).filter(p => Array.isArray(p.native_action_ids) && p.native_action_ids.includes(target));
    const occurrence = scope === 'occurrence' && decision === 'repeat' && members.some(m => m.occurrence_id === member) ? member : row?.source_occurrence_id;
    const choices = row?.ok === true ? ['repeat'] as const : row?.hold_id === gateId && row.unresolved_attempt === true ? ['retry', 'skip', 'manual_complete'] as const : [];
    const overrides: BioXpRecoveryAction['overrides'] = {};
    let valid = true;
    for (const [key, text] of Object.entries(draft)) {
        if (!text) continue;
        if (/^-?\d+$/.test(text) && Number.isSafeInteger(Number(text))) overrides[key] = Number(text);
        else valid = false;
    }
    const action: BioXpRecoveryAction | null = row && decision && choices.some(d => d === decision) && valid ? {
        action: 'recover', ...(scope === 'occurrence' && (decision === 'retry' || decision === 'repeat') ? { scope } : {}), decision, gate_id: gateId, source_occurrence_id: String(occurrence), native_action_id: target,
        expected_attempt: Number(row.attempt), overrides,
    } : null;
    const matches = (p: Preview, a: BioXpRecoveryAction) => p.submitted === false && p.job_id === jobId && p.action === a.action && p.gate_id === a.gate_id && p.expected_attempt === a.expected_attempt && p.source_occurrence_id === a.source_occurrence_id && p.native_action_id === a.native_action_id && p.decision === a.decision && (p.scope ?? 'action') === (a.scope ?? 'action') && Object.keys(p.overrides).length === Object.keys(a.overrides).length && Object.entries(a.overrides).every(([k,v]) => p.overrides[k] === v);
    async function check() {
        if (!action) return;
        const version = ++revision.current; setError('');
        try {
            const result = await methodsPost<Preview>(`runs/${encodeURIComponent(jobId)}/recovery-preview`, action, { expected_connection_generation: generation });
            if (version !== revision.current) return;
            if (!matches(result, action) || !Array.isArray(result.attempt_results) || !result.attempt_results.length || !Array.isArray(result.sibling_results) || !Array.isArray(result.editable_settings)) throw new Error('Recovery preview does not match this attempt or lacks attempt evidence.');
            setPreview(result);
        } catch (e) { if (version === revision.current) { setPreview(null); setError(bioXpErrorText(e)); } }
    }
    return <section aria-label="Same-run recovery"><h4>Same-run recovery</h4>
        <p>Load the method report for this hold. Select a reported attempt, then preview exactly which actions would be replayed.</p>
        <label>Reported attempt<select aria-label="Reported attempt" value={target} onChange={e => { reset(); setMember(''); setScope('action'); setTarget(e.target.value); setDecision(''); }}><option value="">Select an attempt…</option>{Array.from(latest.entries()).filter(([,r]) => r.ok === true || r.hold_id === gateId && r.unresolved_attempt === true).map(([id,r]) => <option key={id} value={id}>{String(r.source_occurrence_id)} · {id} · attempt {String(r.attempt)}</option>)}</select></label>
        {row && <label>Recovery decision<select aria-label="Recovery decision" value={decision} onChange={e => { reset(); setDecision(e.target.value as typeof decision); }}><option value="">Choose a decision…</option>{choices.map(d => <option key={d} value={d}>{labels[d]}</option>)}</select></label>}
        {(decision === 'retry' || decision === 'repeat') && <label>Redo scope<select aria-label="Redo scope" value={scope} onChange={e => { reset(); setScope(e.target.value as typeof scope); }}><option value="action">Selected native action only</option><option value="occurrence">Whole authored step (this occurrence)</option></select></label>}
        {scope === 'occurrence' && decision === 'repeat' && members.length > 1 && <label>Target occurrence<select aria-label="Target occurrence" value={String(occurrence)} onChange={e => { reset(); setMember(e.target.value); }}>{members.map(m => <option key={String(m.occurrence_id)} value={String(m.occurrence_id)}>{String(m.step_id)} · {String(m.occurrence_id)}</option>)}</select></label>}
        <button type="button" disabled={disabled || !action} onClick={() => void check()}>Preview decision</button>
        {error && <p role="alert">{error}</p>}
        {preview && <><h5>{preview.scope === 'occurrence' ? 'Whole occurrence' : 'Selected action'}</h5>
            <ol>{preview.affected_actions?.map(a => <li key={a.action_id}>{a.kind.replaceAll('_', ' ')} · {a.action_id}</li>)}</ol>
            {preview.projected_target != null && <p>Only this sequence target: {JSON.stringify(preview.projected_target)}</p>}
            {preview.scope === 'occurrence' && <p>Previously completed children in this occurrence may run again. A repeat returns to the hold; it does not continue the job.</p>}<p>Attempt {preview.expected_attempt} · {preview.native_action_id}</p>
            <details><summary>Attempt history and partial effects</summary><pre>{JSON.stringify({ selected: preview.attempt_results, siblings: preview.sibling_results })}</pre></details>
            <p>Edits apply to this attempt only. Blank inherits the prior setting. Positive Z offsets go deeper; zero is an explicit setting.</p>
            {(decision === 'retry' || decision === 'repeat') && preview.editable_settings.map(key => <label key={key}>{key.replaceAll('_', ' ')} · {String(preview.previous_settings?.[key] ?? 'not reported')} → {String(preview.proposed_settings?.[key] ?? 'inherit')}<input aria-label={key} inputMode={key.endsWith('offset_steps') ? 'text' : 'numeric'} placeholder="Inherit" value={draft[key] ?? ''} onChange={e => { revision.current++; setDraft({ ...draft, [key]: e.target.value }); }} /></label>)}
            <p>Unknown physical effects remain unknown. Skipping or recording operator completion does not verify liquid movement.</p>
            <button type="button" disabled={disabled || !action || !matches(preview, action)} onClick={() => { if (action && matches(preview, action)) send(action); }}>Submit recovery decision</button></>}
    </section>;
}
