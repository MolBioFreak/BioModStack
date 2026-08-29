import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type {
    ProjectHubExperimentSummary,
    ProjectHubDNASequenceSummary,
    ProjectHubReadModel,
} from '../../lib/api';

function readableKind(kind: ProjectHubExperimentSummary['kind']): string {
    const labels: Record<ProjectHubExperimentSummary['kind'], string> = {
        pcr: 'PCR',
        restriction_digest: 'Restriction digest',
        alignment: 'Alignment',
        sequence_change: 'Sequence change',
        ligation: 'Ligation assembly',
        gibson: 'Gibson assembly',
        golden_gate: 'Golden Gate assembly',
    };
    return labels[kind];
}

function dateLabel(value: string): string {
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.valueOf()) ? value : timestamp.toLocaleString();
}

function RelationLink({ href, children }: { href: string | null; children: ReactNode }) {
    if (!href) return <span className="font-medium text-content">{children}</span>;
    return <Link to={href} className="font-medium text-accent hover:underline">{children}</Link>;
}

function DNASequenceCard({ sequence, model }: { sequence: ProjectHubDNASequenceSummary; model: ProjectHubReadModel }) {
    const sequenceData = model.sequence_data.items.filter((item) => item.plasmid_sequence_id === sequence.sequence_id);
    const experiments = model.experiments.filter((item) => item.plasmid_sequence_ids.includes(sequence.sequence_id));
    const results = model.results.filter((item) => item.plasmid_sequence_id === sequence.sequence_id);
    const hasRelationships = sequenceData.length > 0 || experiments.length > 0 || results.length > 0;

    return (
        <article className="overflow-hidden rounded-2xl border border-border-primary bg-surface shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-primary bg-surface-secondary px-4 py-3">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-base font-semibold text-content">{sequence.name}</h3>
                        <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">Latest editable · Revision {sequence.revision_number}</span>
                    </div>
                    <p className="mt-1 text-xs text-content-secondary">
                        {sequence.length_bp.toLocaleString()} bp · {sequence.topology ?? 'unknown topology'} · {sequence.feature_count} features
                    </p>
                    {sequence.description ? <p className="mt-2 line-clamp-2 text-xs text-content-muted">{sequence.description}</p> : null}
                </div>
                <Link to={sequence.reopen_href} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white outline-none focus:ring-2 focus:ring-accent">Open latest</Link>
            </div>

            {hasRelationships ? (
                <div className="grid gap-3 p-3 lg:grid-cols-3">
                    {sequenceData.length > 0 ? (
                        <section aria-label={`${sequence.name} NGS data`} className="rounded-xl border border-border-primary bg-surface-secondary p-3">
                            <h4 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-sky-300">NGS data</h4>
                            <div className="mt-2 space-y-2">
                                {sequenceData.map((item) => (
                                    <div key={item.id} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs">
                                        <RelationLink href={item.reopen_href}>{item.title}</RelationLink>
                                        <p className="mt-1 text-content-muted">{item.kind.replace('_', ' ')} · {item.status}</p>
                                        {item.summary ? <p className="mt-1 text-content-secondary">{item.summary}</p> : null}
                                    </div>
                                ))}
                            </div>
                        </section>
                    ) : null}

                    {experiments.length > 0 ? (
                        <section aria-label={`${sequence.name} saved experiments`} className="rounded-xl border border-border-primary bg-surface-secondary p-3">
                            <h4 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-violet-300">Saved experiments</h4>
                            <div className="mt-2 space-y-2">
                                {experiments.map((item) => {
                                    const inputs = item.input_sequence_ids ?? item.plasmid_sequence_ids;
                                    const outputs = item.output_sequence_ids ?? [];
                                    const isInput = inputs.includes(sequence.sequence_id);
                                    const isOutput = outputs.includes(sequence.sequence_id);
                                    const relation = isInput && isOutput
                                        ? 'Input and output DNA sequence'
                                        : isOutput
                                            ? 'Output DNA sequence'
                                            : isInput
                                                ? 'Input DNA sequence'
                                                : 'Related DNA sequence';
                                    return (
                                        <div key={item.id} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs">
                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                                <RelationLink href={item.reopen_href}>{item.title}</RelationLink>
                                                <span className="rounded-full border border-border-primary px-2 py-0.5 text-[10px] text-content-muted">{relation}</span>
                                            </div>
                                            <p className="mt-1 text-content-muted">{readableKind(item.kind)} · {item.status}</p>
                                            {item.plasmid_sequence_ids.length > 1 ? <p className="mt-1 text-content-secondary">Connects {item.plasmid_sequence_ids.length} DNA sequences</p> : null}
                                        </div>
                                    );
                                })}
                            </div>
                        </section>
                    ) : null}

                    {results.length > 0 ? (
                        <section aria-label={`${sequence.name} results`} className="rounded-xl border border-border-primary bg-surface-secondary p-3">
                            <h4 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-emerald-300">Results</h4>
                            <div className="mt-2 space-y-2">
                                {results.map((item) => (
                                    <div key={item.id} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs">
                                        <RelationLink href={item.reopen_href}>{item.type}</RelationLink>
                                        <p className="mt-1 text-content-muted">{item.status} · {dateLabel(item.created_at)}</p>
                                        {item.summary ? <p className="mt-1 text-content-secondary">{item.summary}</p> : null}
                                    </div>
                                ))}
                            </div>
                        </section>
                    ) : null}
                </div>
            ) : (
                <div className="px-4 py-3 text-xs text-content-muted">No NGS data, saved experiments, or results are linked to this DNA sequence yet.</div>
            )}

            <details className="border-t border-border-primary px-4 py-2 text-[10px] text-content-muted">
                <summary className="cursor-pointer font-medium text-content-secondary">Identity and membership details</summary>
                <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                    <div><dt>Sequence ID</dt><dd className="break-all font-mono text-content-secondary">{sequence.sequence_id}</dd></div>
                    <div><dt>Latest revision ID</dt><dd className="break-all font-mono text-content-secondary">{sequence.revision_id}</dd></div>
                    <div><dt>Project receipt</dt><dd className="break-all font-mono text-content-secondary">{sequence.receipt_id}</dd></div>
                    <div><dt>Latest revision SHA-256</dt><dd className="break-all font-mono text-content-secondary">{sequence.current_content_sha256 ?? 'Unavailable'}</dd></div>
                    <div><dt>Project membership digest</dt><dd className="break-all font-mono text-content-secondary">{sequence.content_digest}</dd></div>
                </dl>
            </details>
        </article>
    );
}

function UnassignedProjectRecords({ model }: { model: ProjectHubReadModel }) {
    const known = new Set(model.plasmids.map((sequence) => sequence.sequence_id));
    const sequenceData = model.sequence_data.items.filter((item) => !known.has(item.plasmid_sequence_id));
    const experiments = model.experiments.filter((item) => !item.plasmid_sequence_ids.some((sequenceId) => known.has(sequenceId)));
    const results = model.results.filter((item) => !item.plasmid_sequence_id || !known.has(item.plasmid_sequence_id));
    if (sequenceData.length === 0 && experiments.length === 0 && results.length === 0) return null;

    return (
        <section aria-label="Unassigned Project records" className="mt-4 rounded-xl border border-amber-500/40 bg-amber-500/5 p-4">
            <h2 className="text-sm font-semibold text-content">Unassigned Project records</h2>
            <p className="mt-1 text-xs text-content-secondary">These records belong to the Project but do not identify one of its current DNA sequences.</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {sequenceData.map((item) => <div key={`data:${item.id}`} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs"><RelationLink href={item.reopen_href}>{item.title}</RelationLink><p className="mt-1 text-content-muted">NGS data · {item.status}</p></div>)}
                {experiments.map((item) => <div key={`experiment:${item.id}`} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs"><RelationLink href={item.reopen_href}>{item.title}</RelationLink><p className="mt-1 text-content-muted">{readableKind(item.kind)} · {item.status}</p></div>)}
                {results.map((item) => <div key={`result:${item.id}`} className="rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs"><RelationLink href={item.reopen_href}>{item.type}</RelationLink><p className="mt-1 text-content-muted">Result · {item.status}</p></div>)}
            </div>
        </section>
    );
}

export function DNASequenceProjectWorkspace({ model }: { model: ProjectHubReadModel }) {
    return (
        <section aria-label="DNA sequence relationship workspace" className="min-h-0 flex-1 overflow-auto bg-surface p-3 sm:p-4">
            <div className="mx-auto max-w-7xl">
                <header className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">DNA sequence workspace</p>
                        <h2 className="mt-1 text-lg font-semibold text-content">{model.project.plasmid_count} current DNA sequences</h2>
                        <p className="mt-1 max-w-3xl text-xs text-content-secondary">Each DNA sequence opens at its latest editable revision. A sequence can be a plasmid, linear construct, amplicon, assembly product, or reference. NGS evidence, saved experiments, and derived results stay with the sequence that owns them.</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Link to={model.sequence_data.launcher_href} className="rounded-lg border border-border-primary px-3 py-2 text-xs font-semibold text-content-secondary hover:text-content">Open NGS workspace</Link>
                        <Link to={model.project.add_plasmid_href} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white">Add DNA sequence</Link>
                    </div>
                </header>
                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    {model.plasmids.map((sequence) => <DNASequenceCard key={sequence.sequence_id} sequence={sequence} model={model} />)}
                </div>
                <UnassignedProjectRecords model={model} />
                {model.activity.length > 0 ? (
                    <details className="mt-4 rounded-xl border border-border-primary bg-surface-secondary px-4 py-3 text-xs">
                        <summary className="cursor-pointer font-semibold text-content">Recent Project activity</summary>
                        <div className="mt-3 space-y-2">
                            {model.activity.slice(0, 10).map((item) => (
                                <div key={item.id} className="flex flex-wrap justify-between gap-2 border-t border-border-primary pt-2 first:border-0 first:pt-0">
                                    <span className="text-content-secondary">{item.summary}</span>
                                    <span className="text-content-muted">{dateLabel(item.occurred_at)}</span>
                                </div>
                            ))}
                        </div>
                    </details>
                ) : null}
            </div>
        </section>
    );
}
