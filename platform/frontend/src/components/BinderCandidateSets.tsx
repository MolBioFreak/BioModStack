import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchCandidateSets, saveCandidateSet, type CandidateRef } from '../lib/binderSelectedRound';

/** Persist frozen members and operator order; never alter producer rank or verdict. */
export function BinderCandidateSets({ jobId, candidates, ranking, filterState = {}, onLoad }: {
    jobId: string; candidates: CandidateRef[]; ranking?: unknown; filterState?: Record<string, unknown>;
    onLoad: (refs: CandidateRef[]) => void;
}) {
    const [name, setName] = useState('');
    const [loaded, setLoaded] = useState<{ id: string; revision: number }>();
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const sets = useQuery({ queryKey: ['binder-candidate-sets', jobId], queryFn: () => fetchCandidateSets(jobId), retry: false });
    const save = async (final?: boolean) => {
        setBusy(true); setError('');
        try {
            await saveCandidateSet(jobId, { name: name.trim() || 'Selected candidates', candidate_refs: candidates,
                design_ids: candidates.flatMap(ref => ref.kind === 'design' ? [ref.design_id] : []),
                filter_state: filterState, ...(ranking !== undefined ? { ranking } : {}),
                revision: (loaded?.revision ?? 0) + 1,
                ...(final !== undefined ? { final_designation: { designated: final, ...(loaded ? { supersedes_set_id: loaded.id } : {}) } } : {}) });
            const result = await sets.refetch();
            if (result.isError) throw Error('Set saved; readback unavailable.');
        } catch (reason) { setError(String(reason)); }
        finally { setBusy(false); }
    };
    return <details><summary>Named sets and final designation</summary><div className="flex flex-wrap gap-2 py-2">
        <label>Set name<input aria-label="Candidate set name" value={name} onChange={event => setName(event.target.value)} /></label>
        <button type="button" disabled={busy} onClick={() => void save()}>Save set ({candidates.length})</button>
        <button type="button" disabled={busy} onClick={() => void save(true)}>Mark final</button>
        <button type="button" disabled={busy} onClick={() => void save(false)}>Remove final</button>
        <p>Order is frozen separately from native rank. Final designation does not change reuse or native outcome.</p>
        {sets.data?.map(set => <button type="button" key={set.id} onClick={() => { setName(set.name); setLoaded({ id: set.id, revision: set.revision ?? 1 }); onLoad(set.candidate_refs ?? (set.design_ids ?? []).map(design_id => ({ kind: 'design', job_id: jobId, design_id }))); }}>Load {set.name}{set.revision !== undefined ? ` · revision ${set.revision}` : ''}{set.final_designation?.designated ? ' · final' : ''}</button>)}
        {sets.isError && <p role="status">Saved sets unavailable. <button type="button" onClick={() => void sets.refetch()}>Refresh sets</button></p>}
        {error && <p role="alert">{error}</p>}
    </div></details>;
}
