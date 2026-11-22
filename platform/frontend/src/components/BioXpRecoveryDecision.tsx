import { useRef, useState } from 'react';
import { bioXpErrorText, type BioXpRecoveryAction } from '../lib/bioxpClient';
import { methodsPost, type MethodValue } from '../lib/bioxpMethods';
import { object } from './BioXpMethodFields';

const supportedEdits = new Set(['aspiration_delay_ms', 'on_contact_delay_ms', 'submerge_steps']);
const labels = { retry: 'Retry failed action', repeat: 'Repeat completed action', skip: 'Skip failed action', manual_complete: 'Mark completed by operator' };
type Preview = BioXpRecoveryAction & { job_id: string; submitted: boolean; previous_settings: MethodValue; proposed_settings: MethodValue; editable_settings: string[]; attempt_results: unknown[]; sibling_results: unknown[] };

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
    const [target, setTarget] = useState('');
    const [decision, setDecision] = useState<BioXpRecoveryAction['decision'] | ''>('');
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [preview, setPreview] = useState<Preview | null>(null);
    const [error, setError] = useState('');
    const revision = useRef(0);
    const reset = () => { revision.current++; setPreview(null); setDraft({}); setError(''); };
    const row = latest.get(target);
    const choices = row?.ok === true ? ['repeat'] as const : row?.hold_id === gateId && row.unresolved_attempt === true ? ['retry', 'skip', 'manual_complete'] as const : [];
    const overrides: BioXpRecoveryAction['overrides'] = {};
    let valid = true;
    for (const [key, text] of Object.entries(draft)) {
        if (!text) continue;
        if (/^\d+$/.test(text) && Number.isSafeInteger(Number(text))) overrides[key] = Number(text);
        else valid = false;
    }
    const action: BioXpRecoveryAction | null = row && decision && choices.some(d => d === decision) && valid ? {
        action: 'recover', decision, gate_id: gateId, source_occurrence_id: String(row.source_occurrence_id), native_action_id: target,
        expected_attempt: Number(row.attempt), overrides,
    } : null;
    const matches = (p: Preview, a: BioXpRecoveryAction) => p.submitted === false && p.job_id === jobId && p.action === a.action && p.gate_id === a.gate_id && p.expected_attempt === a.expected_attempt && p.source_occurrence_id === a.source_occurrence_id && p.native_action_id === a.native_action_id && p.decision === a.decision && Object.keys(p.overrides).length === Object.keys(a.overrides).length && Object.entries(a.overrides).every(([k,v]) => p.overrides[k] === v);
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
        <p>Load the method report for this hold. Select one reported action; other actions in its occurrence are not replayed.</p>
        <label>Reported attempt<select aria-label="Reported attempt" value={target} onChange={e => { reset(); setTarget(e.target.value); setDecision(''); }}><option value="">Select an attempt…</option>{Array.from(latest.entries()).filter(([,r]) => r.ok === true || r.hold_id === gateId && r.unresolved_attempt === true).map(([id,r]) => <option key={id} value={id}>{String(r.source_occurrence_id)} · {id} · attempt {String(r.attempt)}</option>)}</select></label>
        {row && <label>Recovery decision<select aria-label="Recovery decision" value={decision} onChange={e => { reset(); setDecision(e.target.value as typeof decision); }}><option value="">Choose a decision…</option>{choices.map(d => <option key={d} value={d}>{labels[d]}</option>)}</select></label>}
        <button type="button" disabled={disabled || !action} onClick={() => void check()}>Preview decision</button>
        {error && <p role="alert">{error}</p>}
        {preview && <><p>Selected attempt evidence: {JSON.stringify(preview.attempt_results)}</p><p>Other actions in this occurrence: {JSON.stringify(preview.sibling_results)}</p>
            <p>Previous settings: {JSON.stringify(preview.previous_settings)} · Previewed settings: {JSON.stringify(preview.proposed_settings)}</p>
            {(decision === 'retry' || decision === 'repeat') && preview.editable_settings.filter(key => supportedEdits.has(key)).map(key => <label key={key}>{key.replaceAll('_', ' ')} (attempt only)<input aria-label={key} inputMode="numeric" placeholder="Inherit" value={draft[key] ?? ''} onChange={e => { revision.current++; setDraft({ ...draft, [key]: e.target.value }); }} /></label>)}
            <p>Unknown physical effects remain unknown. Skipping or recording operator completion does not verify liquid movement.</p>
            <button type="button" disabled={disabled || !action || !matches(preview, action)} onClick={() => { if (action && matches(preview, action)) send(action); }}>Submit recovery decision</button></>}
    </section>;
}
