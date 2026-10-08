import { useEffect, type ReactNode } from 'react';
import { MobileMolBioToolbar } from './MobileMolBioToolbar';
import type { SequenceData } from './types';
import type { MolBioMobileSurface } from './utils/mobileLayout';

export interface MobileMolBioWorkup {
    job_id: string;
    scientific_status: 'PASS' | 'FAIL' | 'REVIEW';
    revision_relation: 'current' | 'historical';
    manifest_available: boolean;
}

export function parseMobileMolBioWorkups(payload: unknown): MobileMolBioWorkup[] | null {
    if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { workups?: unknown }).workups)) {
        return null;
    }
    const parsed: MobileMolBioWorkup[] = [];
    for (const candidate of (payload as { workups: unknown[] }).workups) {
        if (!candidate || typeof candidate !== 'object') return null;
        const workup = candidate as Record<string, unknown>;
        if (
            typeof workup.job_id !== 'string'
            || workup.job_id.trim().length === 0
            || !['PASS', 'FAIL', 'REVIEW'].includes(String(workup.scientific_status))
            || !['current', 'historical'].includes(String(workup.revision_relation))
            || typeof workup.manifest_available !== 'boolean'
        ) {
            return null;
        }
        parsed.push({
            job_id: workup.job_id,
            scientific_status: workup.scientific_status as MobileMolBioWorkup['scientific_status'],
            revision_relation: workup.revision_relation as MobileMolBioWorkup['revision_relation'],
            manifest_available: workup.manifest_available,
        });
    }
    return parsed;
}

export type MobileMolBioWorkupStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

interface MobileMolBioWorkspaceProps {
    constructName: string;
    digestIdentity: string;
    digestAvailable: boolean;
    qcAvailable: boolean;
    error?: string | null;
    hasSequence: boolean;
    constructPickerOpen: boolean;
    surface: MolBioMobileSurface;
    onBack: () => void;
    onOpenConstructs: () => void;
    onSurfaceChange: (surface: MolBioMobileSurface) => void;
    constructs: ReactNode;
    map: ReactNode;
    sequence: ReactNode;
    details: ReactNode;
    digest: ReactNode;
    qc: ReactNode;
}

interface MobileMolBioReadPanelProps {
    mode: 'details' | 'qc';
    sequenceData: SequenceData;
    workups: MobileMolBioWorkup[];
    workupsStatus: MobileMolBioWorkupStatus;
}

export function MobileMolBioWorkspace({
    constructName,
    digestIdentity,
    digestAvailable,
    qcAvailable,
    error,
    hasSequence,
    constructPickerOpen,
    surface,
    onBack,
    onOpenConstructs,
    onSurfaceChange,
    constructs,
    map,
    sequence,
    details,
    digest,
    qc,
}: MobileMolBioWorkspaceProps) {
    useEffect(() => {
        const className = 'bms-molbio-mobile-active';
        document.documentElement.classList.add(className);
        return () => document.documentElement.classList.remove(className);
    }, []);

    const showConstructs = constructPickerOpen || !hasSequence;
    const analysisUnavailable = (!digestAvailable && surface === 'digest')
        || (!qcAvailable && surface === 'qc');
    const authorizedSurface = analysisUnavailable ? 'map' : surface;
    const activeSurface = showConstructs ? 'constructs' : authorizedSurface;
    const digestVisible = digestAvailable && hasSequence && !showConstructs && authorizedSurface === 'digest';
    let content: ReactNode = null;
    if (showConstructs) content = constructs;
    else if (authorizedSurface === 'map') content = map;
    else if (authorizedSurface === 'sequence') content = sequence;
    else if (authorizedSurface === 'details') content = details;
    else if (authorizedSurface === 'qc') content = qc;

    return (
        <div
            data-molbio-mobile-layout="true"
            className="fixed inset-0 z-[80] flex h-[100dvh] w-screen flex-col overflow-hidden bg-slate-950 text-slate-100"
        >
            <MobileMolBioToolbar
                constructName={constructName}
                digestAvailable={digestAvailable}
                qcAvailable={qcAvailable}
                hasSequence={hasSequence}
                surface={authorizedSurface}
                onBack={onBack}
                onOpenConstructs={onOpenConstructs}
                onSurfaceChange={onSurfaceChange}
            />
            <main
                data-molbio-mobile-surface={activeSurface}
                style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 1rem)' }}
                className="relative min-h-0 flex-1 overflow-hidden bg-slate-900"
            >
                {error && (
                    <div
                        data-molbio-mobile-error="true"
                        role="alert"
                        aria-live="polite"
                        className="pointer-events-none absolute inset-x-3 top-3 z-50 max-h-24 overflow-hidden rounded-lg border border-red-700 bg-red-950/95 px-3 py-2 text-sm text-red-100 shadow-xl"
                    >
                        Error: {error}
                    </div>
                )}
                {content}
                {digestAvailable && hasSequence && (
                    <div
                        key={digestIdentity}
                        data-molbio-persistent-digest="true"
                        hidden={!digestVisible}
                        aria-hidden={!digestVisible}
                        className="h-full min-h-0 overflow-hidden"
                    >
                        {digest}
                    </div>
                )}
            </main>
        </div>
    );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
    return (
        <section className="rounded-xl border border-slate-700 bg-slate-900/70 p-3">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
            {children}
        </section>
    );
}

export function MobileMolBioReadPanel({
    mode,
    sequenceData,
    workups,
    workupsStatus,
}: MobileMolBioReadPanelProps) {
    if (mode === 'qc') {
        return (
            <div className="h-full overflow-y-auto overscroll-contain px-3 pt-3 pb-[max(env(safe-area-inset-bottom),0.75rem)]">
                <div className="mb-3 flex items-center justify-between">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-100">Sequencing QC</h2>
                        <p className="text-xs text-slate-400">{sequenceData.name || 'Current construct'}</p>
                    </div>
                    <span className="rounded-full border border-slate-600 px-2 py-1 text-xs text-slate-300">Read only</span>
                </div>
                {workupsStatus === 'loading' ? (
                    <div className="rounded-xl border border-slate-700 p-5 text-center text-sm text-slate-400">
                        Loading sequencing QC workups…
                    </div>
                ) : workupsStatus === 'unavailable' ? (
                    <div role="alert" className="rounded-xl border border-red-800 bg-red-950/60 p-5 text-center text-sm text-red-200">
                        Sequencing QC workups could not be loaded.
                    </div>
                ) : workupsStatus === 'idle' ? (
                    <div className="rounded-xl border border-slate-700 p-5 text-center text-sm text-slate-400">
                        Sequencing QC linkage is unavailable for this construct.
                    </div>
                ) : workups.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-slate-700 p-5 text-center text-sm text-slate-400">
                        No sequencing QC workups are linked to this construct.
                    </div>
                ) : (
                    <div className="space-y-2">
                        {workups.map((workup) => (
                            <article key={workup.job_id} className="rounded-xl border border-slate-700 bg-slate-900/70 p-3">
                                <div className="flex items-center justify-between gap-3">
                                    <span className={`rounded-full px-2 py-1 text-xs font-semibold ${
                                        workup.scientific_status === 'PASS'
                                            ? 'bg-emerald-500/15 text-emerald-200'
                                            : workup.scientific_status === 'FAIL'
                                                ? 'bg-red-500/15 text-red-200'
                                                : 'bg-amber-500/15 text-amber-200'
                                    }`}>
                                        {workup.scientific_status}
                                    </span>
                                    <span className="text-xs text-slate-400">
                                        {workup.revision_relation === 'current' ? 'Current revision' : 'Historical revision'}
                                    </span>
                                </div>
                                <div className="mt-2 font-mono text-xs text-slate-300">{workup.job_id}</div>
                                <div className="mt-1 text-xs text-slate-400">
                                    {workup.manifest_available ? 'Manifest available' : 'Manifest unavailable'}
                                </div>
                            </article>
                        ))}
                    </div>
                )}
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto overscroll-contain px-3 pt-3 pb-[max(env(safe-area-inset-bottom),0.75rem)]">
            <div className="mb-3 flex items-center justify-between gap-3">
                <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold text-slate-100">{sequenceData.name || 'Current construct'}</h2>
                    <p className="text-xs text-slate-400">
                        {sequenceData.sequence.length.toLocaleString()} bp · {sequenceData.circular ? 'Circular' : 'Linear'} · {sequenceData.sequenceType.toUpperCase()}
                    </p>
                </div>
                <span className="flex-shrink-0 rounded-full border border-slate-600 px-2 py-1 text-xs text-slate-300">Read only</span>
            </div>

            <div className="space-y-3">
                <DetailSection title={`Features (${sequenceData.features?.length || 0})`}>
                    {sequenceData.features?.length ? (
                        <div className="space-y-2">
                            {sequenceData.features.map((feature) => (
                                <article key={feature.id} className="rounded-lg bg-slate-800/70 p-2">
                                    <div className="font-medium text-slate-100">{feature.name || feature.type}</div>
                                    <div className="mt-1 text-xs text-slate-400">
                                        {(feature.start + 1).toLocaleString()}–{feature.end.toLocaleString()} · {feature.type}
                                    </div>
                                </article>
                            ))}
                        </div>
                    ) : (
                        <p className="text-sm text-slate-400">No annotated features.</p>
                    )}
                </DetailSection>

                <DetailSection title={`Primers (${sequenceData.primers?.length || 0})`}>
                    {sequenceData.primers?.length ? (
                        <div className="space-y-2">
                            {sequenceData.primers.map((primer) => (
                                <article key={primer.id} className="rounded-lg bg-slate-800/70 p-2">
                                    <div className="font-medium text-slate-100">{primer.name}</div>
                                    <div className="mt-1 break-all font-mono text-xs text-slate-300">{primer.sequence}</div>
                                    <div className="mt-1 text-xs text-slate-400">
                                        {primer.tm !== undefined ? `Tm ${primer.tm.toFixed(1)} °C` : 'Tm unavailable'}
                                    </div>
                                </article>
                            ))}
                        </div>
                    ) : (
                        <p className="text-sm text-slate-400">No primers linked to this construct.</p>
                    )}
                </DetailSection>
            </div>
        </div>
    );
}
