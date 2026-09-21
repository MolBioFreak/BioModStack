import { useEffect, useState } from 'react';
import { initialExecutionPolicy, newJobExecutionPolicy, normalizeResultPolicy, saveNewJobExecutionPolicy, setDraftExecutionPolicy } from '../lib/executionPolicy';

export function ExecutionPolicyControl({ initialPolicy }: { initialPolicy?: ReturnType<typeof initialExecutionPolicy> } = {}) {
    const [policy, setPolicy] = useState(() => initialPolicy ?? initialExecutionPolicy());
    const [saved, setSaved] = useState(() => newJobExecutionPolicy().remote_result_policy);
    useEffect(() => {
        setDraftExecutionPolicy(policy);
        return () => setDraftExecutionPolicy(undefined);
    }, [policy]);
    return <section aria-label="Result return policy" className="mb-5 space-y-2 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4">
        <label className="block text-xs font-medium text-[var(--text-secondary)]" htmlFor="remote-result-policy">Successful remote results</label>
        <select id="remote-result-policy" aria-label="Successful remote results" className="block w-full max-w-xl rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]" value={policy.remote_result_policy} onChange={event => setPolicy({ remote_result_policy: normalizeResultPolicy(event.target.value) })}>
            <option value="manual">Manual — ask before pulling (default)</option>
            <option value="automatic">Automatic — pull after successful completion</option>
        </select>
        <p className="text-xs leading-relaxed text-[var(--text-muted)]">Saved with this job. Failed transfers require explicit retry; failure diagnostics are always manual. This does not change scientific settings.</p>
        <button type="button" className="rounded-lg border border-[var(--border-secondary)] bg-[var(--bg-tertiary)] px-3 py-1.5 text-xs text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-control-strong)]" onClick={() => { saveNewJobExecutionPolicy(policy); setSaved(policy.remote_result_policy); }}>Use this policy for new jobs in this browser</button>
        <p className="text-xs leading-relaxed text-[var(--text-muted)]">New-job default in this browser: {saved}. Clones and retries preserve the saved job policy.</p>
    </section>;
}
