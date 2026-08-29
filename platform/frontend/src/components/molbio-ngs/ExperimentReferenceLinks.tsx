import { useQuery } from '@tanstack/react-query';
import { fetchMolBioNgsStateRevision, type DomainStateMember } from '../../lib/api';

interface ExperimentReferenceLinksProps {
    domainExperimentId: string | null;
    stateRevisionId: string | null;
    title?: string;
}

function exactReference(member: DomainStateMember) {
    if (member.entity_kind !== 'molecular_revision') return null;
    const destination = member.reopen_destination;
    if (!destination || typeof destination !== 'object' || Array.isArray(destination)) return null;
    const params = (destination as { params?: unknown }).params;
    if (!params || typeof params !== 'object' || Array.isArray(params)) return null;
    const sequenceId = (params as Record<string, unknown>).sequence_id;
    const revisionId = (params as Record<string, unknown>).revision_id;
    if (typeof sequenceId !== 'string' || typeof revisionId !== 'string') return null;
    return { sequenceId, revisionId, receiptId: member.receipt_id, digest: member.content_digest };
}

export default function ExperimentReferenceLinks({ domainExperimentId, stateRevisionId, title = 'Exact Experiment references' }: ExperimentReferenceLinksProps) {
    const revisionQuery = useQuery({
        queryKey: ['molbio-ngs-state-revision', domainExperimentId, stateRevisionId],
        queryFn: () => fetchMolBioNgsStateRevision(domainExperimentId as string, stateRevisionId as string),
        enabled: Boolean(domainExperimentId && stateRevisionId),
        retry: false,
    });
    const references = (revisionQuery.data?.members ?? []).map(exactReference).filter((value): value is NonNullable<typeof value> => Boolean(value));
    if (!domainExperimentId || !stateRevisionId) return null;
    return (
        <div className="rounded-md border border-border-primary bg-surface p-3" data-testid="experiment-reference-result-links">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-content-muted">{title}</h4>
            <p className="mt-1 text-xs text-content-muted">These exact immutable molecular revisions supplied the selected Experiment state. Open them in the shared molecular viewer without following a mutable current head.</p>
            {revisionQuery.isError ? <p role="alert" className="mt-2 text-xs text-error">Exact Experiment references could not be loaded.</p> : references.length === 0 ? <p className="mt-2 text-xs text-content-muted">No exact molecular reference is attached to this state revision.</p> : <div className="mt-2 flex flex-wrap gap-2">
                {references.map((reference) => <a
                    key={reference.receiptId}
                    className="rounded-md border border-border-primary bg-surface-secondary px-3 py-2 text-xs font-medium text-content-primary hover:border-primary/60"
                    href={`/designer?molbio_sequence_id=${encodeURIComponent(reference.sequenceId)}&molbio_revision_id=${encodeURIComponent(reference.revisionId)}`}
                >Open {reference.sequenceId.slice(0, 8)} · r{reference.revisionId.slice(0, 8)} · {reference.digest.slice(0, 10)}</a>)}
            </div>}
        </div>
    );
}
