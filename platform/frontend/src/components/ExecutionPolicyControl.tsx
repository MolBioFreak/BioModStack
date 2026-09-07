import { useEffect, useState } from 'react';
import { initialExecutionPolicy, newJobExecutionPolicy, normalizeResultPolicy, saveNewJobExecutionPolicy, setDraftExecutionPolicy } from '../lib/executionPolicy';

export function ExecutionPolicyControl({ initialPolicy }: { initialPolicy?: ReturnType<typeof initialExecutionPolicy> } = {}) {
    const [policy, setPolicy] = useState(() => initialPolicy ?? initialExecutionPolicy());
    const [saved, setSaved] = useState(() => newJobExecutionPolicy().remote_result_policy);
    useEffect(() => {
        setDraftExecutionPolicy(policy);
        return () => setDraftExecutionPolicy(undefined);
    }, [policy]);
    return <section aria-label="Result return policy" className="mb-5 rounded-xl border border-slate-700 p-4">
        <label>Successful remote results
            <select aria-label="Successful remote results" value={policy.remote_result_policy} onChange={event => setPolicy({ remote_result_policy: normalizeResultPolicy(event.target.value) })}>
                <option value="manual">Manual — ask before pulling (default)</option>
                <option value="automatic">Automatic — pull after successful completion</option>
            </select>
        </label>
        <p>Saved with this job. Failed transfers require explicit retry; failure diagnostics are always manual. This does not change scientific settings.</p>
        <button type="button" onClick={() => { saveNewJobExecutionPolicy(policy); setSaved(policy.remote_result_policy); }}>Use this policy for new jobs in this browser</button>
        <p>New-job default in this browser: {saved}. Clones and retries preserve the saved job policy.</p>
    </section>;
}
