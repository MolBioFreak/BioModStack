import type { ProjectManagerReadModel } from '../../lib/projectManager';
import { displayLabel, valueText } from './projectManagerState';

interface ProjectInspectorProps {
    summary: ProjectManagerReadModel;
    selectionUnavailable?: string | null;
    busy?: boolean;
    onClose?: () => void;
    onOpenCanonical: () => void;
    onOpenNgsMolBio?: () => void;
    onAddExisting: () => void;
    onCreateDomain: () => void;
    onEdit: () => void;
    onArchive: () => void;
    onRestore: () => void;
    onRecord: () => void;
}

function DetailGrid({ value }: { value: Record<string, unknown> }) {
    const entries = Object.entries(value).filter(([, item]) => item !== null && item !== undefined && item !== '').slice(0, 14);
    if (!entries.length) return <p className="text-xs text-content-muted">No additional fields were supplied by the current revision.</p>;
    return (
        <dl className="grid grid-cols-1 gap-2">
            {entries.map(([key, item]) => (
                <div key={key} className="rounded-lg border border-border-primary bg-surface px-3 py-2">
                    <dt className="text-[9px] font-semibold uppercase tracking-[0.14em] text-content-muted">{displayLabel(key)}</dt>
                    <dd className="mt-1 break-words text-xs text-content-secondary">{valueText(item)}</dd>
                </div>
            ))}
        </dl>
    );
}

export function ProjectInspector({
    summary,
    selectionUnavailable,
    busy,
    onClose,
    onOpenCanonical,
    onOpenNgsMolBio,
    onAddExisting,
    onCreateDomain,
    onEdit,
    onArchive,
    onRestore,
    onRecord,
}: ProjectInspectorProps) {
    const selection = summary.selection;
    const treeSelection = summary.tree.nodes.find((node) => node.node_key === selection.node_key);
    const actions = new Set([...selection.available_actions, ...(treeSelection?.allowed_actions ?? [])]);
    const surface = selection.canonical_surface;
    const identity = selection.canonical_identity;
    const reconciliationIssue = selection.reconciliation.state !== 'current';
    const canOpen = actions.has('open') || Boolean(surface?.available_actions.includes('open'));
    const isWorkflowDomain = selection.node_type === 'domain_experiment'
        && (selection.summary.schema === 'bms.protein-in-silico-experiment.v3'
            || selection.summary.schema === 'bms.ngs-molbio-experiment.v2'
            || selection.summary.schema === 'bms.ngs-molbio-experiment.v1'
            || Array.isArray(selection.summary.planned_capability_ids));

    return (
        <aside aria-label="Selected node inspector" aria-busy={busy || undefined} className="flex h-full min-h-0 flex-col border-l border-border-primary bg-surface-secondary">
            <header className="border-b border-border-primary px-4 py-4">
                <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Selected {displayLabel(selection.node_type)}</p>
                        <h2 className="mt-1 truncate text-lg font-semibold text-content">{selection.title}</h2>
                        {selection.subtitle && <p className="mt-1 text-xs leading-5 text-content-secondary">{selection.subtitle}</p>}
                    </div>
                    {onClose && <button type="button" onClick={onClose} className="rounded-md border border-border-primary px-2 py-1 text-xs text-content-secondary xl:hidden">Close</button>}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                    {selection.node_type === 'domain_experiment' && actions.has('attach') && (
                        <button type="button" onClick={onAddExisting} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-surface-secondary">Add existing</button>
                    )}
                    {selection.node_type === 'global_experiment' && (
                        <button type="button" onClick={onCreateDomain} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white outline-none focus:ring-2 focus:ring-accent">New Domain Experiment</button>
                    )}
                    {canOpen && (
                        <button type="button" onClick={onOpenCanonical} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white outline-none focus:ring-2 focus:ring-accent">Open canonical source</button>
                    )}
                    {isWorkflowDomain && onOpenNgsMolBio && (
                        <button type="button" onClick={onOpenNgsMolBio} className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-white outline-none focus:ring-2 focus:ring-accent">Open Plans &amp; Runs workspace</button>
                    )}
                    {actions.has('edit') && <button type="button" onClick={onEdit} className="rounded-lg border border-border-primary px-3 py-2 text-xs font-semibold text-content-secondary hover:text-content focus:ring-2 focus:ring-accent">Edit revision</button>}
                    {(actions.has('add_note') || ['project', 'global_experiment', 'domain_experiment'].includes(selection.node_type)) && <button type="button" onClick={onRecord} className="rounded-lg border border-border-primary px-3 py-2 text-xs font-semibold text-content-secondary hover:text-content focus:ring-2 focus:ring-accent">Add record</button>}
                    {actions.has('archive') && <button type="button" onClick={onArchive} className="rounded-lg border border-warning/60 px-3 py-2 text-xs font-semibold text-warning focus:ring-2 focus:ring-warning">Archive</button>}
                    {actions.has('restore') && <button type="button" onClick={onRestore} className="rounded-lg border border-success/60 px-3 py-2 text-xs font-semibold text-success focus:ring-2 focus:ring-success">Restore</button>}
                </div>
            </header>

            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
                {selectionUnavailable && (
                    <section role="status" className="rounded-xl border border-warning/60 bg-warning/10 p-3 text-xs text-content-secondary">
                        <p className="font-semibold text-warning">Selection unavailable</p>
                        <p className="mt-1">{selectionUnavailable}</p>
                    </section>
                )}

                <section>
                    <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Canonical identity</h3>
                    <div className="rounded-xl border border-border-primary bg-surface p-3">
                        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[11px]">
                            <dt className="text-content-muted">Store</dt><dd className="break-all font-mono text-content-secondary">{identity.store_id ?? 'global'}</dd>
                            <dt className="text-content-muted">Entity</dt><dd className="break-all font-mono text-content-secondary">{identity.entity_id ?? summary.subject_id}</dd>
                            {identity.entity_kind && <><dt className="text-content-muted">Kind</dt><dd className="text-content-secondary">{displayLabel(identity.entity_kind)}</dd></>}
                            {identity.receipt_id && <><dt className="text-content-muted">Receipt</dt><dd className="break-all font-mono text-content-secondary">{identity.receipt_id}</dd></>}
                            {identity.content_digest && <><dt className="text-content-muted">Digest</dt><dd className="break-all font-mono text-[9px] text-content-secondary">sha256:{identity.content_digest}</dd></>}
                        </dl>
                    </div>
                </section>

                <section>
                    <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Authority &amp; reconciliation</h3>
                    <div className="rounded-xl border p-3 text-xs" style={{ borderColor: reconciliationIssue ? 'var(--warning)' : 'var(--border-primary)', backgroundColor: reconciliationIssue ? 'color-mix(in srgb, var(--warning) 9%, var(--bg-primary))' : 'var(--bg-primary)' }}>
                        <div className="flex items-center justify-between gap-2">
                            <span className="font-semibold text-content">{displayLabel(selection.reconciliation.state)}</span>
                            <span className="text-[10px] text-content-muted">{selection.reconciliation.last_verified_at ? `Verified ${new Date(selection.reconciliation.last_verified_at).toLocaleString()}` : 'Global authority'}</span>
                        </div>
                        {selection.reconciliation.reason && <p className="mt-2 text-content-secondary">{selection.reconciliation.reason}</p>}
                    </div>
                </section>

                {surface && (
                    <section>
                        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Canonical result surface</h3>
                        <div className="rounded-xl border border-border-primary bg-surface p-3 text-xs">
                            <div className="flex flex-wrap gap-2">
                                <span className="rounded-full bg-accent/15 px-2 py-1 font-semibold text-accent">{displayLabel(surface.surface_kind)}</span>
                                <span className="rounded-full border border-border-primary px-2 py-1 text-content-secondary">{displayLabel(surface.readiness)}</span>
                                <span className="rounded-full border border-border-primary px-2 py-1 text-content-secondary">Scientific: {displayLabel(surface.scientific_acceptance.state)}</span>
                            </div>
                            {surface.scientific_acceptance.reason && <p className="mt-2 text-content-secondary">{surface.scientific_acceptance.reason}</p>}
                            {!surface.route && <p className="mt-2 font-medium text-warning">The server did not issue a canonical route for this surface.</p>}
                        </div>
                    </section>
                )}

                <section>
                    <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Revision summary</h3>
                    <DetailGrid value={selection.summary as Record<string, unknown>} />
                </section>

                {Object.keys(selection.scientific_context).length > 0 && (
                    <section>
                        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Scientific context</h3>
                        <DetailGrid value={selection.scientific_context as Record<string, unknown>} />
                    </section>
                )}

                <section>
                    <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-content-muted">Read-model provenance</h3>
                    <div className="rounded-xl border border-border-primary bg-surface p-3 text-[10px] text-content-muted">
                        <p>Generation {summary.subject_generation} · assembled {new Date(summary.assembled_at).toLocaleString()}</p>
                        <p className="mt-1 break-all font-mono">Digest set: {summary.source_digest_set_sha256}</p>
                        <p className="mt-1">{summary.source_receipt_ids.length} verified source receipt(s) · {summary.adapter_versions.length} adapter version(s)</p>
                    </div>
                </section>
            </div>
        </aside>
    );
}
