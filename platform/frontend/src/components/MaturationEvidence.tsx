const object = (value: unknown): Record<string, unknown> =>
    value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const count = (value: unknown) => finite(value) && Number.isInteger(value) && value >= 0 ? String(value) : 'unavailable';

/** Display producer coverage, not an alignment or a browser-side ranking engine. */
export function MaturationEvidence({ comparisons, completeness }: { comparisons: unknown; completeness: unknown }) {
    const rank = object(completeness);
    return <div className="mt-3 space-y-3 text-xs" data-maturation-evidence>
        {typeof rank.paper_rank_available === 'boolean' && <div>
            PPIFlow paper rank: {rank.paper_rank_available ? 'available' : `unavailable — ${typeof rank.paper_rank_reason_code === 'string' ? rank.paper_rank_reason_code : 'missing_rank_reason'}`}
        </div>}
        {Object.entries(object(comparisons)).map(([domain, value]) => {
            const record = object(value);
            const complete = record.reason === null && record.reference_coverage === 1 && record.candidate_coverage === 1
                && finite(record.matched_count) && record.matched_count > 0
                && record.expected_reference_count === record.matched_count && record.expected_candidate_count === record.matched_count
                && Array.isArray(record.unmatched_reference) && record.unmatched_reference.length === 0
                && Array.isArray(record.unmatched_candidate) && record.unmatched_candidate.length === 0
                && !record.subset && finite(record.value);
            const subset = object(record.subset);
            return <section key={domain} className="rounded-lg bg-slate-950/40 p-3">
                <div>{domain} — Full-domain RMSD: {complete ? `${record.value} angstrom` : `unavailable — ${typeof record.reason === 'string' ? record.reason : 'invalid_comparison_evidence'}`}</div>
                <div>Reference: {count(record.matched_count)} / {count(record.expected_reference_count)}; Candidate: {count(record.matched_count)} / {count(record.expected_candidate_count)}</div>
                <div>Reference coverage: {finite(record.reference_coverage) ? String(record.reference_coverage) : 'unavailable'}; Candidate coverage: {finite(record.candidate_coverage) ? String(record.candidate_coverage) : 'unavailable'}</div>
                {typeof subset.name === 'string' && finite(subset.value) && <div>Subset {subset.name}: {subset.value} angstrom (not full-domain RMSD)</div>}
                {(['reference', 'candidate'] as const).map(role => {
                    const missing = record[`unmatched_${role}`];
                    return Array.isArray(missing) && missing.length > 0 && <details key={role}>
                        <summary>Unmatched {role} identities</summary>
                        <ul>{missing.map((entry, index) => {
                            const item = object(entry);
                            return <li key={index}>{JSON.stringify(item.identity)} — {typeof item.reason === 'string' ? item.reason : 'missing_unmatched_reason'}</li>;
                        })}</ul>
                    </details>;
                })}
            </section>;
        })}
    </div>;
}
