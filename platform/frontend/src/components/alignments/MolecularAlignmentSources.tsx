import { useEffect, useState } from 'react';
import { fetchMolecularRevision, fetchMolecularRevisionSummaries } from '../../lib/api';
import type { NucleotideSequenceListItem } from '../MolBioToolkit/types';

export const MOLECULAR_ALIGNMENT_PARAM = 'sequence_revision';

/** The current revision is not always the newest one (e.g. after a revert), so page until found. */
export async function currentRevisionId(sequenceId: string): Promise<string | undefined> {
    for (let offset = 0; offset < 5000; offset += 50) {
        const page = await fetchMolecularRevisionSummaries(sequenceId, 50, offset);
        const current = page.find(row => row.is_current);
        if (current) return current.revision_id;
        if (page.length < 50) return undefined;
    }
    return undefined;
}

/** Pin saved records before leaving the shelf; URLs contain identities, never sequence bytes. */
export async function molecularAlignmentHref(sequences: NucleotideSequenceListItem[]): Promise<string> {
    if (sequences.length < 2) throw new Error('Select at least two saved sequences.');
    if (sequences.length > 200) throw new Error('Select up to 200 sequences at a time.');
    const molecule = sequences[0].sequence_type;
    if (sequences.some(sequence => sequence.sequence_type !== molecule)) throw new Error('Select DNA sequences together, or RNA sequences together.');
    const sources = await Promise.all(sequences.map(async sequence => {
        const revisionId = sequence.revision_id ?? await currentRevisionId(sequence.id);
        if (!revisionId) throw new Error(`No saved revision is available for ${sequence.name}. Save it and try again.`);
        return JSON.stringify([sequence.id, revisionId]);
    }));
    const query = new URLSearchParams({model: 'family_msa_nucleic', molecule});
    sources.forEach(source => query.append(MOLECULAR_ALIGNMENT_PARAM, source));
    return `/alignments/new?${query}`;
}

async function loadSources(sources: string[]) {
    if (sources.length > 200) throw new Error('Select up to 200 sequences at a time.');
    return Promise.all(sources.map(async (source, index) => {
        const identity: unknown = JSON.parse(source);
        if (!Array.isArray(identity) || identity.length !== 2 || identity.some(id => typeof id !== 'string' || !id.trim())) {
            throw new Error('Invalid saved sequence link. Select the sequences again in MolBio Viewer.');
        }
        const [sequenceId, revisionId] = identity as [string, string];
        const revision = await fetchMolecularRevision(sequenceId, revisionId);
        if (revision.sequence_id !== sequenceId || revision.revision_id !== revisionId) throw new Error('The returned sequence does not match the selected revision.');
        const {sequence, sequence_type: molecule, is_circular: circular} = revision.snapshot;
        if (typeof sequence !== 'string' || !sequence || (molecule !== 'dna' && molecule !== 'rna') || typeof circular !== 'boolean' || !/^[a-f0-9]{64}$/i.test(revision.content_sha256)) {
            throw new Error(`The saved revision for ${revision.document_name} is incomplete.`);
        }
        const evidence = {kind: 'molecular_revision', sequence_id: sequenceId, revision_id: revisionId,
            content_sha256: revision.content_sha256, topology: circular ? 'circular' : 'linear', molecule};
        return {source, molecule, revisionNumber: revision.revision_number, evidence, row: {
            row_id: `molecular_${String(index + 1).padStart(8, '0')}`,
            header: typeof revision.snapshot.name === 'string' ? revision.snapshot.name : revision.document_name,
            sequence, source_member_id: JSON.stringify([sequenceId, revisionId]), source_offset: 0,
            transformation_provenance: JSON.stringify({...evidence, operation: 'saved_sequence_import'}),
        }};
    }));
}

export function useMolecularAlignmentSources(sources: string[]) {
    const [attempt, setAttempt] = useState(0);
    const key = JSON.stringify([sources, attempt]);
    const [result, setResult] = useState<{key: string; data?: Awaited<ReturnType<typeof loadSources>>; error?: Error}>({key: ''});
    useEffect(() => {
        let cancelled = false;
        if (sources.length) void loadSources(sources).then(data => {
            if (!cancelled) setResult({key, data});
        }).catch(error => {
            if (!cancelled) setResult({key, error: error instanceof Error ? error : new Error(String(error))});
        });
        return () => { cancelled = true; };
        // The key includes every source identity and the explicit retry counter.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key]);
    const current = result.key === key ? result : undefined;
    return {data: current?.data, error: current?.error, isPending: sources.length > 0 && !current, refetch: () => setAttempt(value => value + 1)};
}

export function MolecularAlignmentSourceList({sources, query, onRemove, onClear}: {
    sources: string[]; query: ReturnType<typeof useMolecularAlignmentSources>;
    onRemove: (source: string) => void; onClear: () => void;
}) {
    if (!sources.length) return null;
    return <section aria-label="Sequences from MolBio Viewer" className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
        <h3>Selected sequences ({sources.length})</h3>
        {query.isPending && <p role="status">Loading saved sequences…</p>}
        {query.error && <p role="alert">Could not load the selected sequences: {query.error.message} <button type="button" onClick={() => void query.refetch()}>Retry</button></p>}
        {query.data && <ul>{query.data.map(item => <li key={item.source} className="flex flex-wrap items-center justify-between gap-2 py-1">
            <span>{item.row.header} · {item.row.sequence.length.toLocaleString()} bases · revision {item.revisionNumber}</span>
            <button type="button" aria-label={`Remove ${item.row.header} from alignment`} onClick={() => onRemove(item.source)}>Remove</button>
        </li>)}</ul>}
        <button type="button" onClick={onClear}>Clear selected sequences</button>
    </section>;
}
