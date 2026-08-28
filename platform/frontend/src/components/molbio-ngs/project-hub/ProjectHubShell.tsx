import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ProjectAttachmentDialog, type ProjectAttachmentSource } from '../../project-manager/ProjectAttachmentDialog';
import {
    type ProjectHubExperimentKind,
    type ProjectHubExperimentSummary,
    type ProjectHubPlasmidInfoDraft,
    type ProjectHubPlasmidSummary,
    type ProjectHubReadModel,
} from '../../../lib/api';

const TABS = [
    ['overview', 'Overview'],
    ['plasmids', 'Plasmids'],
    ['sequence-data', 'Sequence Data'],
    ['experiments', 'Experiments'],
    ['results', 'Results'],
    ['activity', 'Activity'],
] as const;

type ProjectHubSection = (typeof TABS)[number][0];

type ProjectHubShellProps = {
    model: ProjectHubReadModel;
    canMutate: boolean;
    mutationBlocker: string | null;
    selectedSection: string | null;
    selectedPlasmidId: string | null;
    onNavigate: (updates: Record<string, string | null | undefined>) => void;
    onSavePlasmidInfo: (plasmid: ProjectHubPlasmidSummary, draft: ProjectHubPlasmidInfoDraft) => Promise<void>;
    saveError: string | null;
    saving: boolean;
};

const PANEL = 'rounded-2xl border border-border-primary bg-surface-secondary shadow-sm';
const BUTTON = 'inline-flex min-h-9 items-center justify-center rounded-lg border border-border-primary bg-surface px-3 py-2 text-xs font-semibold text-content transition-colors hover:border-accent/60 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45';
const PRIMARY = `${BUTTON} border-accent bg-accent text-black hover:bg-accent/90 hover:text-black`;
const MUTED = 'text-content-secondary';

function formatDate(value: string) {
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return value;
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function PlasmidMap({ plasmid, size = 88 }: { plasmid: ProjectHubPlasmidSummary; size?: number }) {
    const radius = 34;
    const circumference = 2 * Math.PI * radius;
    const tones: Record<string, string> = {
        accent: '#24d2e2', success: '#64d989', info: '#5ba6ff', warning: '#ffb454', secondary: '#9f8cff',
    };
    return (
        <Link
            to={plasmid.reopen_href}
            data-testid="plasmid-mini-map"
            aria-label={`Open full plasmid map for ${plasmid.name}, ${plasmid.length_bp.toLocaleString()} bp`}
            className="shrink-0 rounded-full outline-none focus:ring-2 focus:ring-accent"
        >
            <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label={`Miniature circular map for ${plasmid.name}`}>
                <circle cx="50" cy="50" r={radius} fill="none" stroke="currentColor" strokeWidth="8" className="text-border-primary" />
                {plasmid.map_segments.map((segment, index) => {
                    const rawSpan = segment.end >= segment.start
                        ? segment.end - segment.start
                        : plasmid.length_bp - segment.start + segment.end;
                    const span = Math.max(0, Math.min(plasmid.length_bp, rawSpan));
                    const dash = plasmid.length_bp > 0 ? span / plasmid.length_bp * circumference : 0;
                    const offset = plasmid.length_bp > 0 ? -(segment.start / plasmid.length_bp * circumference) : 0;
                    const arrowCoordinate = segment.strand === 'reverse' ? segment.start : segment.end;
                    const arrowAngle = plasmid.length_bp > 0 ? (arrowCoordinate / plasmid.length_bp * Math.PI * 2) - Math.PI / 2 : 0;
                    const arrowX = 50 + Math.cos(arrowAngle) * radius;
                    const arrowY = 50 + Math.sin(arrowAngle) * radius;
                    const direction = segment.strand === 'unknown' ? 'strand not recorded' : segment.strand;
                    const featureLabel = segment.label || plasmid.feature_labels[index] || `Feature ${index + 1}`;
                    const accessibleLabel = `${featureLabel}, ${segment.feature_type}, ${segment.start.toLocaleString()} to ${segment.end.toLocaleString()} bp, ${direction}`;
                    return (
                        <g key={`${segment.start}-${segment.end}-${index}`} tabIndex={0} role="img" aria-label={accessibleLabel} data-feature-label={featureLabel}>
                            <title>{accessibleLabel}</title>
                            <circle cx="50" cy="50" r={radius} fill="none" stroke={tones[segment.tone] ?? tones.accent} strokeWidth="8" strokeLinecap="round" strokeDasharray={`${dash} ${Math.max(0, circumference - dash)}`} strokeDashoffset={offset} transform="rotate(-90 50 50)" />
                            <circle data-feature-direction={segment.strand} cx={arrowX} cy={arrowY} r="2.5" fill={tones[segment.tone] ?? tones.accent} />
                        </g>
                    );
                })}
                <circle cx="50" cy="50" r="27" className="fill-surface-secondary" />
                <text x="50" y="49" textAnchor="middle" className="fill-content text-[8px] font-bold">{plasmid.name}</text>
                <text x="50" y="59" textAnchor="middle" className="fill-content-muted text-[5px]">{plasmid.length_bp.toLocaleString()} bp</text>
            </svg>
        </Link>
    );
}

function TechnicalDetails({ model }: { model: ProjectHubReadModel }) {
    const identity = model.identity;
    return (
        <details data-testid="project-technical-details" className={`${PANEL} mt-4 px-4 py-3 text-xs`}>
            <summary className="cursor-pointer select-none font-semibold text-content-secondary">Provenance and technical details</summary>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                    ['Project / workspace ID', identity.workspace_id],
                    ['Global Experiment ID', identity.global_experiment_id],
                    ['Domain Experiment ID', identity.domain_experiment_id],
                    ['Selected state revision', identity.selected_state_revision_id],
                    ['Current state revision', identity.current_state_revision_id],
                    ['State head generation', identity.state_head_generation],
                    ['Global domain revision', identity.global_domain_revision_id],
                    ['Membership graph SHA-256', identity.membership_graph_sha256],
                    ['Binding', identity.binding_status],
                    ['Adapter', identity.adapter_status],
                ].map(([label, value]) => (
                    <div key={label} className="min-w-0 rounded-lg border border-border-primary bg-surface p-3">
                        <dt className="text-[10px] font-semibold uppercase tracking-wide text-content-muted">{label}</dt>
                        <dd className="mt-1 break-all font-mono text-content-secondary">{String(value)}</dd>
                        <button
                            type="button"
                            aria-label={`Copy ${label}`}
                            className="mt-2 text-[10px] font-semibold text-accent"
                            onClick={() => {
                                if (navigator.clipboard) void navigator.clipboard.writeText(String(value)).catch(() => undefined);
                            }}
                        >Copy</button>
                    </div>
                ))}
            </dl>
            <h3 className="mt-5 font-semibold text-content">Plasmid technical records</h3>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
                {model.plasmids.map((plasmid) => (
                    <section key={plasmid.sequence_id} className="min-w-0 rounded-lg border border-border-primary bg-surface p-3">
                        <h4 className="font-semibold text-content">{plasmid.name}</h4>
                        <dl className="mt-2 space-y-2">
                            {[
                                ['molecular document ID', plasmid.sequence_id],
                                ['molecular revision ID', plasmid.revision_id],
                                ['receipt ID', plasmid.receipt_id],
                                ['receipt SHA-256', plasmid.receipt_sha256],
                                ['content digest', plasmid.content_digest],
                                ['source store', plasmid.source_store_id],
                                ['schema', plasmid.schema_name],
                                ['exact reopen destination', plasmid.reopen_href],
                            ].map(([label, value]) => (
                                <div key={label}>
                                    <dt className="text-[10px] font-semibold uppercase tracking-wide text-content-muted">{label}</dt>
                                    <dd className="break-all font-mono text-content-secondary">{value}</dd>
                                    <button
                                        type="button"
                                        aria-label={`Copy ${plasmid.name} ${label}`}
                                        className="mt-1 text-[10px] font-semibold text-accent"
                                        onClick={() => {
                                            if (navigator.clipboard) void navigator.clipboard.writeText(value).catch(() => undefined);
                                        }}
                                    >Copy</button>
                                </div>
                            ))}
                        </dl>
                    </section>
                ))}
            </div>
        </details>
    );
}

function Metric({ value, label }: { value: ReactNode; label: string }) {
    return <div className="min-w-0 px-1"><strong className="block text-sm text-content">{value}</strong><span className="text-[9px] font-semibold uppercase tracking-wide text-content-muted">{label}</span></div>;
}

function Presence({ label, value, suffix = 'Present' }: { label: string; value: boolean | null; suffix?: string }) {
    return (
        <div className="flex items-center justify-between gap-3 border-b border-border-primary py-2 text-[11px] last:border-b-0">
            <span className={MUTED}>{label}</span>
            <strong className="text-content">{value === null ? 'Not recorded' : value ? <><span className="mr-1 text-success">●</span>{suffix}</> : '—'}</strong>
        </div>
    );
}

function PlasmidCard({ plasmid, canMutate, onEdit, onCompare, onDetails, onAttach }: { plasmid: ProjectHubPlasmidSummary; canMutate: boolean; onEdit: (invoker: HTMLButtonElement) => void; onCompare: () => void; onDetails: () => void; onAttach: () => void }) {
    const unavailable = plasmid.availability === 'unavailable';
    return (
        <article className={`${PANEL} flex min-h-[430px] min-w-0 flex-col p-4`}>
            <div className="flex items-start gap-3">
                <PlasmidMap plasmid={plasmid} />
                <div className="min-w-0 flex-1 pt-1">
                    <h3 className="truncate text-xl font-bold text-content">{plasmid.name}</h3>
                    <p className="text-xs text-content-secondary">{plasmid.description || 'No description recorded'}</p>
                    <p className="mt-1 text-xs text-content-muted">Current sequence · revision {plasmid.revision_number}</p>
                </div>
                <span className={`rounded-full border px-2 py-1 text-[10px] font-semibold capitalize ${unavailable ? 'border-danger/40 bg-danger/10 text-danger' : 'border-success/40 bg-success/10 text-success'}`}>{plasmid.availability}</span>
            </div>
            {unavailable && <p role="alert" className="mt-3 rounded-lg border border-danger/40 bg-danger/10 p-3 text-xs font-semibold text-danger">{plasmid.unavailable_reason ?? 'Molecular member unavailable'}</p>}
            <div className="mt-3 flex min-h-12 flex-wrap content-start gap-1.5">
                {plasmid.feature_labels.slice(0, 4).map((label) => <span key={label} className="rounded-md border border-border-primary bg-surface px-2 py-1 text-[9px] text-content-secondary">{label}</span>)}
            </div>
            <div className="mt-3 grid grid-cols-3 divide-x divide-border-primary border-y border-border-primary py-2">
                <Metric value={`${plasmid.length_bp.toLocaleString()} bp`} label="Length" />
                <Metric value={plasmid.gc_percent === null ? '—' : `${plasmid.gc_percent.toFixed(2)}%`} label="GC" />
                <Metric value={`${plasmid.feature_count} features`} label="Features" />
            </div>
            <div className="mt-2">
                <Presence label="CMV promoter" value={plasmid.cmv_promoter} />
                <Presence label="NeoR / KanR" value={plasmid.neor_kanr} />
                <Presence label="Replication origins" value={plasmid.replication_origin_count === null ? null : plasmid.replication_origin_count > 0} suffix={`${plasmid.replication_origin_count ?? 0} annotated`} />
                <div className="flex items-center justify-between gap-3 border-b border-border-primary py-2 text-[11px]">
                    <span className={MUTED}>Saved Mol Bio experiments</span>
                    <strong className="text-content">{plasmid.saved_experiment_count ? plasmid.saved_experiment_count : 'None yet'}</strong>
                </div>
            </div>
            <div className="mt-auto grid grid-cols-2 gap-2 pt-4">
                <Link className={PRIMARY} to={plasmid.reopen_href}>Open plasmid</Link>
                <button type="button" className={BUTTON} onClick={onCompare}>Compare</button>
                <button type="button" className={BUTTON} onClick={onDetails}>Plasmid details</button>
                <button type="button" className={BUTTON} disabled={!canMutate || unavailable} onClick={(event) => onEdit(event.currentTarget)}>Edit info</button>
                <button type="button" className={`${BUTTON} col-span-2`} disabled={unavailable} onClick={onAttach}>Add current work to Project</button>
            </div>
        </article>
    );
}

function EmptyInline({ title, detail }: { title: string; detail?: string }) {
    return <div className="rounded-lg border border-dashed border-border-primary bg-surface px-4 py-3"><strong className="block text-sm text-content">{title}</strong>{detail && <span className="mt-1 block text-xs text-content-muted">{detail}</span>}</div>;
}

function Overview({ model, canMutate, onEdit, onAttach, onNavigate }: { model: ProjectHubReadModel; canMutate: boolean; onEdit: (p: ProjectHubPlasmidSummary, invoker: HTMLButtonElement) => void; onAttach: (p: ProjectHubPlasmidSummary) => void; onNavigate: ProjectHubShellProps['onNavigate'] }) {
    return (
        <>
            <div className="mb-3 flex items-end justify-between gap-4">
                <div><h2 className="text-xl font-bold text-content">Plasmids</h2><p className="text-sm text-content-secondary">Project molecular inventory, construct summaries, and saved work.</p></div>
                <button className="text-xs font-semibold text-accent" type="button" onClick={() => onNavigate({ section: 'plasmids', plasmid: null })}>Compare all {model.plasmids.length === 4 ? 'four' : model.plasmids.length}</button>
            </div>
            <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-4">
                {model.plasmids.map((plasmid) => <PlasmidCard key={plasmid.sequence_id} plasmid={plasmid} canMutate={canMutate} onEdit={(invoker) => onEdit(plasmid, invoker)} onCompare={() => onNavigate({ section: 'plasmids', plasmid: plasmid.sequence_id })} onDetails={() => onNavigate({ section: 'plasmids', plasmid: plasmid.sequence_id })} onAttach={() => onAttach(plasmid)} />)}
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-[2fr_1fr]">
                <section className={`${PANEL} p-4`}><h3 className="font-semibold text-content">Recent project activity</h3><div className="mt-3 space-y-3">{model.activity.slice(0, 2).map((event) => <div key={event.id} className="flex gap-3 text-xs"><span className="mt-1 text-accent">●</span><div><strong className="block text-content">{event.summary}</strong><span className="text-content-muted">{formatDate(event.occurred_at)}</span></div></div>)}{model.activity.length === 0 && <EmptyInline title="No project activity yet" />}</div></section>
                <section className={`${PANEL} p-4`}><h3 className="font-semibold text-content">Sequencing</h3><div className="mt-3"><EmptyInline title={model.sequence_data.items.length ? `${model.sequence_data.items.length} sequencing records attached` : 'No sequencing data attached'} detail={model.sequence_data.items.length ? 'Open Sequence Data to inspect project-linked ONT evidence.' : 'This project currently contains molecular sequence data only.'} /></div></section>
            </div>
            <TechnicalDetails model={model} />
        </>
    );
}

function PlasmidsTab({ model, canMutate, onEdit, selectedPlasmidId }: { model: ProjectHubReadModel; canMutate: boolean; onEdit: (p: ProjectHubPlasmidSummary, invoker: HTMLButtonElement) => void; selectedPlasmidId: string | null }) {
    const selected = model.plasmids.find((plasmid) => plasmid.sequence_id === selectedPlasmidId) ?? null;
    return (
        <>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-xl font-bold text-content">Plasmids</h2><p className="text-sm text-content-secondary">Current saved plasmid records, maps, and imported annotations for this project.</p></div>{canMutate && <Link className={PRIMARY} to={model.project.add_plasmid_href}>+ Add plasmid</Link>}</div>
            <section data-testid="project-plasmid-comparison" className={`${PANEL} mb-4 p-4`}>
                <h2 className="text-lg font-bold text-content">{selected ? `Compare ${selected.name} with project plasmids` : 'Compare all project plasmids'}</h2>
                <p className="text-xs text-content-secondary">Current revision metrics appear together for direct review.</p>
                <div className="mt-3 grid gap-3 lg:grid-cols-2 xl:grid-cols-4">{model.plasmids.map((plasmid) => <div key={plasmid.sequence_id} className={`rounded-lg border p-3 ${plasmid.sequence_id === selected?.sequence_id ? 'border-accent bg-accent/10' : 'border-border-primary bg-surface'}`}><strong className="text-content">{plasmid.name}</strong><dl className="mt-2 grid grid-cols-2 gap-2 text-xs"><div><dt className="text-content-muted">Length</dt><dd className="font-semibold text-content">{plasmid.length_bp.toLocaleString()} bp</dd></div><div><dt className="text-content-muted">GC</dt><dd className="font-semibold text-content">{plasmid.gc_percent === null ? '—' : `${plasmid.gc_percent.toFixed(2)}%`}</dd></div><div><dt className="text-content-muted">Features</dt><dd className="font-semibold text-content">{plasmid.feature_count}</dd></div><div><dt className="text-content-muted">Saved work</dt><dd className="font-semibold text-content">{plasmid.saved_experiment_count}</dd></div></dl></div>)}</div>
            </section>
            <div data-testid="project-plasmid-desktop-table" className={`${PANEL} hidden overflow-x-auto lg:block`}>
                <table className="w-full text-left text-xs">
                    <thead className="border-b border-border-primary bg-surface text-[10px] uppercase tracking-wide text-content-muted"><tr><th className="p-3">Map</th><th className="p-3">Plasmid</th><th className="p-3">Length</th><th className="p-3">GC</th><th className="p-3">Features</th><th className="p-3">Saved state</th><th className="p-3">Actions</th></tr></thead>
                    <tbody>{model.plasmids.map((plasmid) => <tr key={plasmid.sequence_id} className="border-b border-border-primary last:border-b-0"><td className="p-3"><PlasmidMap plasmid={plasmid} size={62} /></td><td className="p-3"><strong className="block text-sm text-content">{plasmid.name}</strong><span className="text-content-muted">{plasmid.description}</span></td><td className="p-3 font-semibold text-content">{plasmid.length_bp.toLocaleString()} bp</td><td className="p-3 text-content-secondary">{plasmid.gc_percent === null ? '—' : `${plasmid.gc_percent.toFixed(2)}%`}</td><td className="p-3 text-content-secondary">{plasmid.feature_count}</td><td className="p-3 text-content-secondary">Revision {plasmid.revision_number}</td><td className="p-3"><div className="flex gap-2"><Link className={PRIMARY} to={plasmid.reopen_href}>Open sequence</Link><button type="button" className={BUTTON} disabled={!canMutate || plasmid.availability === 'unavailable'} onClick={(event) => onEdit(plasmid, event.currentTarget)}>Edit info</button></div></td></tr>)}</tbody>
                </table>
            </div>
            <div data-testid="project-plasmid-stacked-records" className="grid gap-3 lg:hidden">{model.plasmids.map((plasmid) => <article key={plasmid.sequence_id} className={`${PANEL} p-4`}><div className="flex items-center gap-3"><PlasmidMap plasmid={plasmid} size={62} /><div><h3 className="font-semibold text-content">{plasmid.name}</h3><p className="text-xs text-content-secondary">{plasmid.length_bp.toLocaleString()} bp · {plasmid.feature_count} features · Revision {plasmid.revision_number}</p></div></div><div className="mt-3 flex gap-2"><Link className={PRIMARY} to={plasmid.reopen_href}>Open sequence</Link><button type="button" className={BUTTON} disabled={!canMutate || plasmid.availability === 'unavailable'} onClick={(event) => onEdit(plasmid, event.currentTarget)}>Edit info</button></div></article>)}</div>
            <div className="mt-4"><h2 className="text-xl font-bold text-content">Feature summaries</h2><p className="text-sm text-content-secondary">Readable annotations from each current sequence revision.</p></div>
            <div className="mt-3 grid gap-3 lg:grid-cols-2 xl:grid-cols-4">{model.plasmids.map((plasmid) => <section key={plasmid.sequence_id} className={`${PANEL} p-4`}><h3 className="font-semibold text-content">{plasmid.name}</h3><div className="mt-2 flex flex-wrap gap-1.5">{plasmid.feature_labels.map((label) => <span key={label} className="rounded-md border border-border-primary bg-surface px-2 py-1 text-[10px] text-content-secondary">{label}</span>)}</div></section>)}</div>
            <TechnicalDetails model={model} />
        </>
    );
}

function SequenceDataTab({ model }: { model: ProjectHubReadModel }) {
    const items = model.sequence_data.items;
    return (
        <>
            <div><h2 className="text-xl font-bold text-content">Sequence Data</h2><p className="text-sm text-content-secondary">ONT plasmid sequencing imported into this project or produced through the NGS system.</p></div>
            {items.length === 0 ? (
                <section className={`${PANEL} mt-4 p-6 text-center lg:p-10`}>
                    <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-accent/40 bg-accent/10 text-2xl text-accent">⌁</div>
                    <h2 className="mt-4 text-xl font-bold text-content">No ONT sequencing data attached</h2>
                    <p className="mx-auto mt-2 max-w-2xl text-sm text-content-secondary">This project currently contains plasmid records only. Import existing ONT data or start plasmid sequencing through the NGS system when data becomes available.</p>
                    <div className="mx-auto mt-6 grid max-w-4xl gap-3 md:grid-cols-2">
                        <article className="rounded-xl border border-border-primary bg-surface p-5 text-left"><h3 className="font-semibold text-content">Import existing sequencing data</h3><p className="mt-2 text-xs leading-relaxed text-content-secondary">Attach ONT FASTQ, BAM, POD5, BLOW5, run manifests, and related files to the correct project plasmid.</p><Link className={`${BUTTON} mt-4`} to={model.sequence_data.import_href}>Import ONT data</Link></article>
                        <article className="rounded-xl border border-border-primary bg-surface p-5 text-left"><h3 className="font-semibold text-content">Run through the NGS system</h3><p className="mt-2 text-xs leading-relaxed text-content-secondary">Start from a project plasmid and carry its exact reference into the supported ONT launch workflow.</p><Link className={`${PRIMARY} mt-4`} to={model.sequence_data.launcher_href}>Open NGS launcher</Link></article>
                    </div>
                </section>
            ) : <div className="mt-4 grid gap-3 md:grid-cols-2">{items.map((item) => <article key={item.id} className={`${PANEL} p-4`}><span className="text-[10px] font-semibold uppercase tracking-wide text-accent">{item.kind.replaceAll('_', ' ')}</span><h3 className="mt-1 font-semibold text-content">{item.title}</h3><p className="mt-2 text-sm text-content-secondary">{item.summary}</p><p className="mt-2 text-xs text-content-muted">{item.plasmid_name} · {formatDate(item.created_at)} · {item.status}</p><Link className={`${BUTTON} mt-3`} to={item.reopen_href}>Open exact record</Link></article>)}</div>}
            <div className="mt-5"><h2 className="text-lg font-bold text-content">What appears here</h2><p className="text-sm text-content-secondary">Project-linked sequencing evidence remains organized by plasmid and run.</p></div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
                ['Runs and read sets', 'Instrument runs, basecalled reads, barcodes, and retained raw signal.'],
                ['Alignment and coverage', 'Reference alignment, depth, strand balance, and structural evidence.'],
                ['Clone assessment', 'Clone summary, wf-clone report, and junction review.'],
                ['Viewer evidence', 'IGV views with best, worst, and anomalous read subsets.'],
            ].map(([title, detail]) => <section key={title} className={`${PANEL} p-4`}><strong className="text-content">{title}</strong><p className="mt-2 text-xs leading-relaxed text-content-secondary">{detail}</p></section>)}</div>
            <TechnicalDetails model={model} />
        </>
    );
}

const EXPERIMENT_LANES: Array<{ kind: ProjectHubExperimentKind; label: string; description: string; empty: string; action: string }> = [
    { kind: 'pcr', label: 'PCR', description: 'Primer design, amplification plans, and saved PCR products.', empty: 'No saved PCR yet', action: 'Start PCR' },
    { kind: 'restriction_digest', label: 'Restriction digests', description: 'Saved enzyme selections, fragments, and digest simulations.', empty: 'No saved restriction digests yet', action: 'New digest' },
    { kind: 'alignment', label: 'Alignments', description: 'Pairwise or multi-sequence alignments saved from Mol Bio Toolkit.', empty: 'No saved alignments yet', action: 'New alignment' },
    { kind: 'sequence_change', label: 'Sequence changes', description: 'Saved edits, annotations, assemblies, and resulting sequence revisions.', empty: 'No later sequence changes yet', action: 'Edit sequence' },
];

function ExperimentsTab({ model, selectedPlasmidId, onNavigate }: { model: ProjectHubReadModel; selectedPlasmidId: string | null; onNavigate: ProjectHubShellProps['onNavigate'] }) {
    const saved = model.experiments.filter((item) => item.persistence === 'saved');
    const visible = selectedPlasmidId ? saved.filter((item) => item.plasmid_sequence_ids.includes(selectedPlasmidId)) : saved;
    return (
        <>
            <div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-xl font-bold text-content">Mol Bio experiments</h2><p className="text-sm text-content-secondary">Saved Mol Bio Toolkit work performed on project plasmids.</p></div><Link className={PRIMARY} to="/molbio">+ Start experiment</Link></div>
            <div className="mt-4 flex flex-wrap gap-2"><button type="button" aria-pressed={!selectedPlasmidId} onClick={() => onNavigate({ plasmid: null })} className={`${BUTTON} ${!selectedPlasmidId ? 'border-accent bg-accent/10 text-accent' : ''}`}>All plasmids</button>{model.plasmids.map((plasmid) => <button key={plasmid.sequence_id} type="button" aria-pressed={selectedPlasmidId === plasmid.sequence_id} onClick={() => onNavigate({ plasmid: plasmid.sequence_id })} className={`${BUTTON} ${selectedPlasmidId === plasmid.sequence_id ? 'border-accent bg-accent/10 text-accent' : ''}`}>{plasmid.name}</button>)}</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2 2xl:grid-cols-4">{EXPERIMENT_LANES.map((lane) => {
                const items = visible.filter((item) => item.kind === lane.kind);
                return <section key={lane.kind} className={`${PANEL} flex min-h-48 flex-col p-4`}><span className="text-[10px] font-semibold uppercase tracking-wide text-accent">{items.length} saved</span><h3 className="mt-1 font-semibold text-content">{lane.label}</h3><p className="mt-2 text-xs leading-relaxed text-content-secondary">{lane.description}</p><div className="mt-3 space-y-2">{items.length ? items.map((item) => <ExperimentItem key={item.id} item={item} />) : <EmptyInline title={lane.empty} />}</div><Link className={`${BUTTON} mt-auto`} to="/molbio">{lane.action}</Link></section>;
            })}</div>
            <section className={`${PANEL} mt-4 p-4`}><h2 className="text-lg font-bold text-content">Sequence revision history</h2><p className="text-sm text-content-secondary">Initial imports and future saved sequence changes appear here.</p><div className="mt-3 grid gap-2 md:grid-cols-2 2xl:grid-cols-4">{model.plasmids.map((plasmid) => <div key={plasmid.sequence_id} className="rounded-lg border border-border-primary bg-surface p-3 text-xs"><strong className="block text-content">{plasmid.name} · Revision {plasmid.revision_number}</strong><span className="mt-1 block text-content-secondary">{plasmid.revision_number === 1 ? 'Initial sequence saved' : 'Current saved sequence revision'}</span><span className="mt-1 block text-content-muted">{plasmid.length_bp.toLocaleString()} bp</span></div>)}</div></section>
            <TechnicalDetails model={model} />
        </>
    );
}

function ExperimentItem({ item }: { item: ProjectHubExperimentSummary }) {
    const body = <><strong className="block text-xs text-content">{item.title}</strong><span className="mt-1 block text-[10px] text-content-muted">{item.plasmid_name} · {formatDate(item.created_at)}</span></>;
    return item.reopen_href ? <Link to={item.reopen_href} className="block rounded-lg border border-border-primary bg-surface p-3 hover:border-accent/50">{body}</Link> : <div className="rounded-lg border border-border-primary bg-surface p-3">{body}</div>;
}

function ResultsTab({ model }: { model: ProjectHubReadModel }) {
    return <><div><h2 className="text-xl font-bold text-content">Results</h2><p className="text-sm text-content-secondary">Persisted project outcomes and exact native result links.</p></div><div className="mt-4 grid gap-3 md:grid-cols-2">{model.results.length ? model.results.map((result) => <article key={result.id} className={`${PANEL} p-4`}><span className="text-[10px] font-semibold uppercase tracking-wide text-accent">{result.type}</span><h3 className="mt-1 font-semibold text-content">{result.plasmid_name}</h3><p className="mt-2 text-sm text-content-secondary">{result.summary ?? 'No readable summary recorded.'}</p><p className="mt-2 text-xs text-content-muted">{result.owner} · {result.status} · {formatDate(result.created_at)}</p><Link className={`${BUTTON} mt-3`} to={result.reopen_href}>Open result</Link></article>) : <EmptyInline title="No results yet" detail="Saved scientific outcomes will appear here." />}</div><TechnicalDetails model={model} /></>;
}

function ActivityTab({ model }: { model: ProjectHubReadModel }) {
    return (
        <>
            <div><h2 className="text-xl font-bold text-content">Activity</h2><p className="text-sm text-content-secondary">Readable project history. Technical event identity stays collapsed.</p></div>
            <div className={`${PANEL} mt-4 divide-y divide-border-primary px-4`}>
                {model.activity.length ? model.activity.map((event) => (
                    <div key={event.id} className="flex gap-3 py-4">
                        <span className="mt-1 text-accent">●</span>
                        <div className="min-w-0 flex-1">
                            <strong className="text-content">{event.summary}</strong>
                            <span className="mt-1 block text-xs text-content-muted">{formatDate(event.occurred_at)}</span>
                            <details data-testid={`activity-technical-${event.id}`} className="mt-2 text-xs text-content-secondary">
                                <summary className="cursor-pointer font-semibold">Technical details</summary>
                                <dl className="mt-2 grid gap-2 sm:grid-cols-3">
                                    <div><dt className="text-content-muted">Event type</dt><dd className="break-all font-mono">{event.technical_event_type}</dd></div>
                                    <div><dt className="text-content-muted">Receipt ID</dt><dd className="break-all font-mono">{event.receipt_id}</dd></div>
                                    <div><dt className="text-content-muted">Envelope SHA-256</dt><dd className="break-all font-mono">{event.envelope_sha256}</dd></div>
                                </dl>
                            </details>
                        </div>
                    </div>
                )) : <div className="py-4"><EmptyInline title="No activity yet" /></div>}
            </div>
            <TechnicalDetails model={model} />
        </>
    );
}

function EditDialog({ plasmid, saving, error, onCancel, onSave }: { plasmid: ProjectHubPlasmidSummary; saving: boolean; error: string | null; onCancel: () => void; onSave: (draft: ProjectHubPlasmidInfoDraft) => Promise<void> }) {
    const dialogRef = useRef<HTMLDivElement>(null);
    const firstRef = useRef<HTMLInputElement>(null);
    const [draft, setDraft] = useState<ProjectHubPlasmidInfoDraft>({
        name: plasmid.name,
        molecule_type: plasmid.molecule_type ?? 'Plasmid · circular dsDNA',
        topology: plasmid.topology ?? 'circular',
        description: plasmid.description,
        organism_host_context: plasmid.organism_host_context,
        project_tags: plasmid.project_tags,
        project_notes: plasmid.project_notes,
    });
    useEffect(() => {
        firstRef.current?.focus();
        const onKeyDown = (event: globalThis.KeyboardEvent) => {
            if (event.key === 'Escape' && !saving) onCancel();
            if (event.key !== 'Tab' || !dialogRef.current) return;
            const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled])'));
            if (!focusable.length) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        };
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [onCancel, saving]);
    const update = <K extends keyof ProjectHubPlasmidInfoDraft>(key: K, value: ProjectHubPlasmidInfoDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
    const submit = async (event: FormEvent) => {
        event.preventDefault();
        try {
            await onSave(draft);
        } catch {
            // The owning mutation renders the governed API error and preserves this draft.
        }
    };
    const input = 'mt-1 w-full rounded-lg border border-border-primary bg-surface px-3 py-2 text-sm text-content outline-none focus:border-accent';
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="presentation">
            <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="project-plasmid-edit-title" aria-describedby="project-plasmid-edit-description" className="w-full max-w-2xl rounded-2xl border border-border-primary bg-surface-secondary p-5 shadow-2xl">
                <div className="flex items-start justify-between gap-4"><div><span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-accent">Edit plasmid information</span><h2 id="project-plasmid-edit-title" className="mt-1 text-2xl font-bold text-content">{plasmid.name}</h2><p id="project-plasmid-edit-description" className="text-xs text-content-secondary">Project metadata for the current sequence record</p></div><button type="button" className={BUTTON} aria-label="Close edit dialog" disabled={saving} onClick={onCancel}>×</button></div>
                <form className="mt-5 grid gap-4 md:grid-cols-2" onSubmit={submit}>
                    <label className="text-xs font-semibold text-content-secondary">Plasmid name<input ref={firstRef} required name="name" className={input} value={draft.name} onChange={(event) => update('name', event.target.value)} /></label>
                    <label className="text-xs font-semibold text-content-secondary">Molecule type<select name="molecule_type" className={input} value={draft.molecule_type} onChange={(event) => update('molecule_type', event.target.value)}><option>Plasmid · circular dsDNA</option><option>Plasmid · linear dsDNA</option><option>Other DNA</option></select></label>
                    <label className="md:col-span-2 text-xs font-semibold text-content-secondary">Description<textarea name="description" className={`${input} min-h-20 resize-y`} value={draft.description} onChange={(event) => update('description', event.target.value)} /></label>
                    <label className="text-xs font-semibold text-content-secondary">Organism / host context<input name="organism_host_context" className={input} placeholder="Not recorded" value={draft.organism_host_context ?? ''} onChange={(event) => update('organism_host_context', event.target.value || null)} /></label>
                    <label className="text-xs font-semibold text-content-secondary">Project tags<input name="project_tags" className={input} value={draft.project_tags.join(', ')} onChange={(event) => update('project_tags', event.target.value.split(',').map((value) => value.trim()).filter(Boolean))} /></label>
                    <label className="md:col-span-2 text-xs font-semibold text-content-secondary">Project notes<textarea name="project_notes" className={`${input} min-h-20 resize-y`} placeholder="Add useful construct notes, intended use, or handling information…" value={draft.project_notes} onChange={(event) => update('project_notes', event.target.value)} /></label>
                    <div className="md:col-span-2 rounded-lg border border-border-primary bg-surface p-3 text-xs text-content-secondary">This edits readable plasmid information through one governed command. Sequence or annotation edits remain in the molecular viewer and create their own sequence revision.</div>
                    {error && <div className="md:col-span-2 rounded-lg border border-error/40 bg-error/10 p-3 text-sm text-error" role="alert">{error}</div>}
                    <div className="md:col-span-2 flex justify-end gap-2"><button type="button" className={BUTTON} disabled={saving} onClick={onCancel}>Cancel</button><button type="submit" className={PRIMARY} disabled={saving}>{saving ? 'Saving…' : 'Save plasmid info'}</button></div>
                </form>
            </div>
        </div>
    );
}

export default function ProjectHubShell({ model, canMutate, mutationBlocker, selectedSection, selectedPlasmidId, onNavigate, onSavePlasmidInfo, saveError, saving }: ProjectHubShellProps) {
    const requested = TABS.some(([key]) => key === selectedSection) ? selectedSection as ProjectHubSection : 'overview';
    const isHistorical = model.identity.selected_state_revision_id !== model.identity.current_state_revision_id;
    const effectiveCanMutate = canMutate && !isHistorical;
    const [editing, setEditing] = useState<ProjectHubPlasmidSummary | null>(null);
    const [attachmentSource, setAttachmentSource] = useState<ProjectAttachmentSource | null>(null);
    const editInvoker = useRef<HTMLElement | null>(null);
    const wasEditing = useRef(false);
    useEffect(() => {
        if (!editing && wasEditing.current) editInvoker.current?.focus();
        wasEditing.current = editing !== null;
    }, [editing]);
    const closeDialog = () => { setEditing(null); };
    const openEdit = (plasmid: ProjectHubPlasmidSummary, invoker: HTMLButtonElement) => { editInvoker.current = invoker; setEditing(plasmid); };
    const handleTabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : event.key === 'ArrowRight' ? (index + 1) % TABS.length : (index - 1 + TABS.length) % TABS.length;
        onNavigate({ section: TABS[next][0], plasmid: null });
        requestAnimationFrame(() => document.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus());
    };
    const content = useMemo(() => {
        if (requested === 'plasmids') return <PlasmidsTab model={model} canMutate={effectiveCanMutate} onEdit={openEdit} selectedPlasmidId={selectedPlasmidId} />;
        if (requested === 'sequence-data') return <SequenceDataTab model={model} />;
        if (requested === 'experiments') return <ExperimentsTab model={model} selectedPlasmidId={selectedPlasmidId} onNavigate={onNavigate} />;
        if (requested === 'results') return <ResultsTab model={model} />;
        if (requested === 'activity') return <ActivityTab model={model} />;
        return <Overview model={model} canMutate={effectiveCanMutate} onEdit={openEdit} onAttach={(plasmid) => setAttachmentSource({ adapterId: 'bms.molbio.member-molecular-revision.adapter.v1', entityId: plasmid.revision_id, label: `${plasmid.name} saved revision ${plasmid.revision_number}`, revision: plasmid.revision_id, digest: plasmid.content_digest, availability: plasmid.availability })} onNavigate={onNavigate} />;
    }, [effectiveCanMutate, model, requested, selectedPlasmidId, onNavigate]);
    return (
        <div className="w-full px-4 pb-6 sm:px-5 lg:px-6">
            <div className="mx-auto w-full max-w-[1400px]">
                <p className="mb-3 text-xs text-content-muted">Projects / NGS &amp; Mol Bio / <strong className="text-content">{model.project.name}</strong></p>
                <header className={`${PANEL} bg-gradient-to-br from-surface-secondary to-accent/5 p-5 lg:p-6`}>
                    <div className="flex flex-wrap items-start gap-5"><div className="min-w-0 flex-1"><p className="text-[10px] font-bold uppercase tracking-[0.18em] text-accent">Local NGS / Mol Bio project</p><h1 className="mt-1 text-3xl font-bold tracking-tight text-content">{model.project.name}</h1><p className="mt-1 text-sm text-content-secondary">{model.project.objective}</p><div className="mt-4 flex flex-wrap gap-2"><span className="rounded-full border border-success/40 bg-success/10 px-3 py-1 text-[10px] font-semibold capitalize text-success">{model.project.lifecycle_state}</span><span className="rounded-full border border-border-primary bg-surface px-3 py-1 text-[10px] text-content-secondary">{model.project.plasmid_count} plasmids</span><span className="rounded-full border border-border-primary bg-surface px-3 py-1 text-[10px] text-content-secondary">Created {formatDate(model.project.created_at)}</span></div></div><div className="flex gap-2"><Link className={BUTTON} to={model.project.settings_href}>Project settings</Link>{effectiveCanMutate && <Link className={PRIMARY} to={model.project.add_plasmid_href}>+ Add plasmid</Link>}</div></div>
                </header>
                {isHistorical && <div className="mt-3 rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning" role="status"><strong>Historical project state — read-only</strong><span className="ml-2 text-content-secondary">Exact reopen links remain available.</span></div>}
                {!isHistorical && !canMutate && mutationBlocker && <div className="mt-3 rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-content-secondary" role="status">{mutationBlocker}</div>}
                <nav role="tablist" aria-label="Project sections" className="mt-4 flex gap-1 overflow-x-auto border-b border-border-primary">{TABS.map(([key, label], index) => <button key={key} type="button" role="tab" id={`project-tab-${key}`} aria-controls={`project-panel-${key}`} aria-selected={requested === key} tabIndex={requested === key ? 0 : -1} onKeyDown={(event) => handleTabKey(event, index)} onClick={() => onNavigate({ section: key, plasmid: null })} className={`shrink-0 border-b-2 px-4 py-3 text-xs font-semibold ${requested === key ? 'border-accent text-content' : 'border-transparent text-content-muted hover:text-content'}`}>{label}</button>)}</nav>
                <main id={`project-panel-${requested}`} role="tabpanel" aria-labelledby={`project-tab-${requested}`} className="mt-5">{content}</main>
            </div>
            {editing && <EditDialog plasmid={editing} saving={saving} error={saveError} onCancel={closeDialog} onSave={async (draft) => { await onSavePlasmidInfo(editing, draft); closeDialog(); }} />}
            <ProjectAttachmentDialog open={attachmentSource !== null} source={attachmentSource ?? undefined} onClose={() => setAttachmentSource(null)} />
        </div>
    );
}
