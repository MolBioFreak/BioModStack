import Plot from 'react-plotly.js';

import type {
    OntFastqQcCheck,
    OntFastqQcJsonValue,
    OntFastqQcResult,
} from '../../lib/ontFastqQcResult';

interface OntFastqQcResultPanelProps {
    result: OntFastqQcResult | null;
    loading: boolean;
    error: string | null;
    onOpenViewer?: (locus?: string) => void;
    onRecoverAccess?: () => void;
    recoveryPending?: boolean;
}

function metric(result: OntFastqQcResult, key: string): string | number | boolean | null {
    return result.alignment[key] ?? result.summary[key] ?? null;
}

function display(value: string | number | boolean | null, suffix = ''): string {
    if (value === null) return '—';
    if (typeof value === 'number') return `${value.toLocaleString()}${suffix}`;
    return `${String(value)}${suffix}`;
}

function displayJsonValue(value: OntFastqQcJsonValue): string {
    if (value === null) return '—';
    if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
    if (typeof value === 'string' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map(displayJsonValue).join(' · ');
    return Object.entries(value)
        .map(([key, nested]) => `${metricLabel(key)}: ${displayJsonValue(nested)}`)
        .join(' · ');
}

function metricLabel(key: string): string {
    return key.replaceAll('_', ' ').replace(/^./, (value) => value.toUpperCase());
}

const CHECKS: Array<{
    key: keyof OntFastqQcResult['verification']['checks'];
    label: string;
    limitation?: string;
}> = [
    {
        key: 'expected_reference_screen',
        label: 'Expected-reference screen',
        limitation: 'Does not establish organism identity or taxonomic contamination exclusion.',
    },
    { key: 'coverage', label: 'Coverage' },
    { key: 'read_support', label: 'Read support' },
    { key: 'sequence_identity', label: 'Sequence identity' },
    { key: 'topology', label: 'Topology' },
];

function CheckCard({
    label,
    limitation,
    check,
}: {
    label: string;
    limitation?: string;
    check: OntFastqQcCheck;
}) {
    const statusClass = check.status === 'pass'
        ? 'text-emerald-300 border-emerald-500/35'
        : check.status === 'fail'
            ? 'text-rose-300 border-rose-500/35'
            : check.status === 'review'
                ? 'text-amber-300 border-amber-500/35'
                : 'text-[var(--text-secondary)] border-[var(--border-primary)]';
    const checkMetrics = Object.entries(check.metrics);
    return (
        <div className={`rounded border bg-[var(--bg-tertiary)] p-3 ${statusClass}`}>
            <div className="flex items-center justify-between gap-2">
                <h5 className="text-sm font-semibold">{label}</h5>
                <span className="rounded border border-current/40 px-2 py-0.5 text-[10px] font-semibold uppercase">{check.status}</span>
            </div>
            <p className="mt-1 text-[11px] text-[var(--text-secondary)]">Purpose: {check.purpose}</p>
            {limitation && <p className="mt-1 text-[10px] text-amber-200">{limitation}</p>}
            {checkMetrics.length > 0 && (
                <dl className="mt-2 grid grid-cols-1 gap-1 text-[11px]">
                    {checkMetrics.map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-3">
                            <dt className="text-[var(--text-secondary)]">
                                {label === 'Topology' && key === 'state' ? 'Topology state' : metricLabel(key)}
                            </dt>
                            <dd className="text-right font-mono text-[var(--text-primary)]">
                                {displayJsonValue(value)}
                                {check.units[key] && !['categorical', 'boolean', 'evidence'].includes(check.units[key])
                                    ? ` ${check.units[key]}`
                                    : ''}
                            </dd>
                        </div>
                    ))}
                </dl>
            )}
            {check.reason_codes.length > 0 && (
                <div className="mt-2 text-[10px] font-mono">{check.reason_codes.join(' · ')}</div>
            )}
        </div>
    );
}

function variantLocus(result: OntFastqQcResult, position: number, end: number): string | null {
    const reference = result.verification.summary.reference_name;
    const length = result.verification.summary.reference_length;
    if (typeof reference !== 'string' || !reference || typeof length !== 'number' || !Number.isInteger(length) || length < 1) {
        return null;
    }
    const start = Math.max(1, position - 100);
    const boundedEnd = Math.min(length, Math.max(end, position) + 100);
    return `${reference}:${start}-${boundedEnd}`;
}

function affectedVariantInterval(variant: OntFastqQcResult['verification']['variants'][number]): string {
    if (variant.kind === 'DEL') {
        return variant.affected_start_1based === variant.affected_end_1based
            ? `Deleted reference base ${variant.affected_start_1based}`
            : `Deleted reference bases ${variant.affected_start_1based}-${variant.affected_end_1based}`;
    }
    if (variant.affected_interval_kind === 'between_bases') {
        return `Between bases after ${variant.affected_start_1based}`;
    }
    return `${variant.affected_start_1based}-${variant.affected_end_1based}`;
}

export function OntFastqQcResultPanel({
    result,
    loading,
    error,
    onOpenViewer,
    onRecoverAccess,
    recoveryPending = false,
}: OntFastqQcResultPanelProps) {
    if (loading) return <p className="text-sm text-[var(--text-secondary)]">Loading validated FASTQ-QC result…</p>;
    if (error) {
        return (
            <div role="alert" className="space-y-2 text-sm text-rose-300">
                <p>{error}</p>
                {onRecoverAccess && (
                    <button
                        type="button"
                        onClick={onRecoverAccess}
                        disabled={recoveryPending}
                        className="rounded border border-rose-400/40 px-3 py-1.5 text-xs font-semibold disabled:opacity-50"
                    >
                        {recoveryPending ? 'Restoring access…' : 'Restore access'}
                    </button>
                )}
            </div>
        );
    }
    if (!result) return <p className="text-sm text-[var(--text-secondary)]">Validated FASTQ-QC result is unavailable.</p>;

    const verification = result.verification;
    const verdictClass = verification.verdict === 'PASS'
        ? 'text-emerald-300 border-emerald-500/40'
        : verification.verdict === 'FAIL'
            ? 'text-rose-300 border-rose-500/40'
            : 'text-amber-300 border-amber-500/40';
    const verdictLabel = verification.verdict === 'REVIEW' ? 'REVIEW REQUIRED' : verification.verdict;
    const coverageFraction = verification.summary.coverage_fraction;
    const sequenceIdentity = verification.summary.sequence_identity_fraction;
    const decisionMinimumDepth = verification.checks.coverage.metrics.minimum_depth;
    const totalReads = metric(result, 'total_reads') ?? metric(result, 'reads_considered');
    const mappedReads = metric(result, 'mapped_reads');
    const referenceLengthValue = verification.summary.reference_length;
    const referenceLength = typeof referenceLengthValue === 'number' ? referenceLengthValue : 0;
    const cards = [
        { label: 'Total reads', value: display(totalReads), detail: 'integer read count' },
        {
            label: 'Mapped reads',
            value: typeof mappedReads === 'number' && typeof totalReads === 'number'
                ? `${mappedReads.toLocaleString()} / ${totalReads.toLocaleString()}`
                : '—',
            detail: 'mapped count over total reads',
        },
        { label: 'Total bases', value: display(metric(result, 'total_bases'), ' bp'), detail: 'sequenced bases' },
        {
            label: 'Reference coverage',
            value: typeof coverageFraction === 'number' ? `${(coverageFraction * 100).toFixed(2)}%` : '—',
            detail: 'bases with ≥1 base-covering alignment record',
        },
        {
            label: 'Decision minimum support depth',
            value: typeof decisionMinimumDepth === 'number' ? decisionMinimumDepth.toLocaleString() : '—',
            detail: 'alignment observations from per-base support; deletion-spanning observations participate',
        },
        {
            label: 'Coverage-envelope minimum',
            value: `${result.coverage.minimum_depth.toLocaleString()} at ${result.coverage.minimum_depth_position_1based.toLocaleString()}`,
            detail: 'base-covering alignment records from samtools depth -aa; deletion bases are excluded',
        },
        {
            label: 'Consensus identity',
            value: typeof sequenceIdentity === 'number' ? `${(sequenceIdentity * 100).toFixed(4)}%` : '—',
            detail: 'observed consensus versus bound reference',
        },
    ];
    const orderedArtifacts = [...result.artifacts].sort((left, right) => left.display_order - right.display_order);
    const artifactRoleGroups = orderedArtifacts.reduce<Array<{
        role: string;
        artifacts: typeof orderedArtifacts;
    }>>((groups, artifact) => {
        const existing = groups.find((group) => group.role === artifact.scientific_role);
        if (existing) existing.artifacts.push(artifact);
        else groups.push({ role: artifact.scientific_role, artifacts: [artifact] });
        return groups;
    }, []);
    const logArtifacts = orderedArtifacts.filter((artifact) => artifact.scientific_role === 'audit_log');
    const histogramX = result.read_length_histogram.bins.map((bin) => (bin.start_bp + bin.end_bp_exclusive) / 2);
    const histogramY = result.read_length_histogram.bins.map((bin) => bin.read_count);
    const coverageX = result.coverage.points.map((point) => point.position_1based);
    const coverageY = result.coverage.points.map((point) => point.depth);

    return (
        <div className="space-y-4" data-testid="ont-fastq-qc-result">
            <section className={`rounded border bg-[var(--bg-secondary)] p-4 ${verdictClass}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div className="text-xs text-[var(--text-secondary)]">{result.job.name}</div>
                        <h4 className="text-lg font-semibold">Scientific verdict: {verdictLabel}</h4>
                        <div className="mt-1 text-xs text-[var(--text-secondary)]">
                            Execution {result.job.status} · ont_fastq_qc · FASTQ · Job {result.job.id}
                        </div>
                    </div>
                    {onOpenViewer && (
                        <button
                            type="button"
                            onClick={() => onOpenViewer()}
                            disabled={result.authority.alignment_readiness !== 'ready'}
                            className="rounded border border-current/40 px-3 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            Open local IGV
                        </button>
                    )}
                </div>
                {verification.reason_codes.length > 0 && (
                    <div className="mt-2 text-xs font-mono">{verification.reason_codes.join(' · ')}</div>
                )}
                <div className="mt-2 text-[11px] text-[var(--text-secondary)]">
                    Manifests {result.authority.manifest_readiness}; alignment {result.authority.alignment_readiness}; artifacts {result.authority.present_artifact_count}/{result.authority.declared_artifact_count} present
                </div>
            </section>

            <section className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7" aria-label="Run summary">
                {cards.map((card) => (
                    <div key={card.label} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2">
                        <div className="text-[11px] text-[var(--text-secondary)]">{card.label}</div>
                        <div className="font-mono text-sm text-[var(--text-primary)]">{card.value}</div>
                        <div className="mt-1 text-[9px] leading-tight text-[var(--text-secondary)]">{card.detail}</div>
                    </div>
                ))}
            </section>

            <section>
                <h4 className="mb-2 text-xs uppercase tracking-wide text-[var(--text-secondary)]">Decision checks</h4>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-5">
                    {CHECKS.map(({ key, label, limitation }) => (
                        <CheckCard key={key} label={label} limitation={limitation} check={verification.checks[key]} />
                    ))}
                </div>
            </section>

            <section className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                    <h4 className="text-sm font-semibold text-[var(--text-primary)]">Read-length distribution</h4>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Purpose: Show read-length shape against the {referenceLength.toLocaleString()} bp reference. Server-derived fixed_width_v1 bins preserve all producer per-read counts without browser rebinning.</p>
                    <p className="mt-1 text-[11px] text-amber-200">Reference marker: {referenceLength.toLocaleString()} bp. Historical producer expected plasmid size: {display(metric(result, 'expected_plasmid_size'), ' bp')}; historical copy-number and multimer metrics do not control this decision.</p>
                    <Plot
                        data={[{
                            type: 'bar',
                            x: histogramX,
                            y: histogramY,
                            marker: { color: '#55a7ff' },
                            hovertemplate: 'Read length %{x:.0f} bp<br>Reads %{y:,}<extra></extra>',
                        }]}
                        layout={{
                            title: { text: 'Read count by read length' },
                            xaxis: { title: { text: 'Read length (bp)' } },
                            yaxis: { title: { text: 'Read count' } },
                            shapes: [{
                                type: 'line',
                                x0: referenceLength,
                                x1: referenceLength,
                                y0: 0,
                                y1: 1,
                                yref: 'paper',
                                line: { color: '#fbbf24', dash: 'dot', width: 1.5 },
                            }],
                            margin: { l: 62, r: 18, t: 44, b: 56 },
                            height: 300,
                            paper_bgcolor: 'rgba(0,0,0,0)',
                            plot_bgcolor: 'rgba(0,0,0,0)',
                            font: { color: '#b8c4d8' },
                        }}
                        config={{ responsive: true, displaylogo: false }}
                        useResizeHandler
                        style={{ width: '100%', height: '300px' }}
                    />
                </div>
                <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                    <h4 className="text-sm font-semibold text-[var(--text-primary)]">Coverage across the construct</h4>
                    <p className="mt-1 text-xs text-[var(--text-secondary)]">Purpose: Identify low aligned-base coverage and the context of review loci. Series: {result.coverage.method}; basis: {result.coverage.depth_basis}; {result.coverage.source_row_count.toLocaleString()} source positions.</p>
                    <p className="mt-1 text-[11px] text-amber-200">
                        Envelope minimum: {result.coverage.minimum_depth.toLocaleString()} at position {result.coverage.minimum_depth_position_1based.toLocaleString()} ({result.coverage.depth_unit}). Decision support minimum: {typeof decisionMinimumDepth === 'number' ? decisionMinimumDepth.toLocaleString() : '—'} (alignment observations; separate per-base-support basis).
                    </p>
                    <Plot
                        data={[{
                            type: 'scattergl',
                            mode: 'lines',
                            x: coverageX,
                            y: coverageY,
                            line: { color: '#63d39b', width: 1.5 },
                            hovertemplate: 'Coordinate %{x:,}<br>Base-covering alignment records %{y:,}<extra></extra>',
                        }]}
                        layout={{
                            title: { text: 'Deletion-excluding aligned-base coverage by eGFP coordinate' },
                            xaxis: { title: { text: 'eGFP coordinate (1-based)' } },
                            yaxis: { title: { text: 'Base-covering alignment records' }, rangemode: 'tozero' },
                            margin: { l: 72, r: 18, t: 44, b: 56 },
                            height: 300,
                            paper_bgcolor: 'rgba(0,0,0,0)',
                            plot_bgcolor: 'rgba(0,0,0,0)',
                            font: { color: '#b8c4d8' },
                        }}
                        config={{ responsive: true, displaylogo: false }}
                        useResizeHandler
                        style={{ width: '100%', height: '300px' }}
                    />
                </div>
            </section>

            <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                <h4 className="mb-2 text-xs uppercase tracking-wide text-[var(--text-secondary)]">Normalized variants</h4>
                {verification.variants.length === 0 ? (
                    <p className="text-sm text-[var(--text-secondary)]">No normalized variants.</p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="border-b border-[var(--border-primary)] text-[var(--text-secondary)]">
                                    <th className="px-2 py-1 text-left">Variant</th>
                                    <th className="px-2 py-1 text-left">Kind</th>
                                    <th className="px-2 py-1 text-left">VCF record</th>
                                    <th className="px-2 py-1 text-left">Affected interval</th>
                                    <th className="px-2 py-1 text-left">Change</th>
                                    <th className="px-2 py-1 text-left">Depth</th>
                                    <th className="px-2 py-1 text-left">Support</th>
                                    <th className="px-2 py-1 text-left">State</th>
                                    <th className="px-2 py-1 text-left">Viewer</th>
                                </tr>
                            </thead>
                            <tbody>
                                {verification.variants.map((variant) => {
                                    const locus = variantLocus(result, variant.affected_start_1based, variant.affected_end_1based);
                                    return (
                                        <tr key={variant.id} className="border-b border-[var(--border-primary)]/40">
                                            <td className="px-2 py-1 font-mono">{variant.id}</td>
                                            <td className="px-2 py-1 font-mono">{variant.kind}</td>
                                            <td className="px-2 py-1 font-mono">{variant.record_start_1based}-{variant.record_end_1based}</td>
                                            <td className="px-2 py-1">{variant.affected_interval_kind}: {affectedVariantInterval(variant)}</td>
                                            <td className="px-2 py-1 font-mono">{variant.ref}→{variant.alt}</td>
                                            <td className="px-2 py-1 font-mono">{variant.depth?.toLocaleString() ?? '—'}</td>
                                            <td className="px-2 py-1 font-mono">
                                                {variant.support_fraction == null ? '—' : `${(variant.support_fraction * 100).toFixed(2)}%`}
                                            </td>
                                            <td className="px-2 py-1">{variant.support_status}</td>
                                            <td className="px-2 py-1">
                                                <button
                                                    type="button"
                                                    disabled={!locus || !onOpenViewer}
                                                    onClick={() => locus && onOpenViewer?.(locus)}
                                                    className="rounded border border-[var(--border-primary)] px-2 py-1 text-[11px] disabled:cursor-not-allowed disabled:opacity-50"
                                                >
                                                    View in IGV
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </section>

            <section className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Governed downloads</h4>
                <div className="mt-2 space-y-3">
                    {artifactRoleGroups.map((group) => (
                        <section key={group.role} data-artifact-role={group.role}>
                            <h5 className="mb-1 text-[11px] font-semibold text-[var(--text-secondary)]">{metricLabel(group.role)}</h5>
                            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                                {group.artifacts.map((artifact) => artifact.state === 'present' ? (
                                    <a
                                        key={`${artifact.source}:${artifact.kind}:${artifact.sha256}`}
                                        href={artifact.url || undefined}
                                        download
                                        data-artifact-display-order={artifact.display_order}
                                        className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-xs hover:border-sky-400/50"
                                    >
                                        <div className="font-semibold text-sky-300">{metricLabel(artifact.kind)}</div>
                                        <div className="mt-1 text-[var(--text-secondary)]">{artifact.source} · {artifact.size_bytes?.toLocaleString()} bytes</div>
                                        <div className="mt-1 font-mono text-[10px] text-[var(--text-secondary)]">SHA-256 {artifact.sha256?.slice(0, 16)}…</div>
                                    </a>
                                ) : (
                                    <div key={`${artifact.source}:${artifact.kind}`} data-artifact-display-order={artifact.display_order} className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-2 text-xs">
                                        <div className="font-semibold text-[var(--text-primary)]">{metricLabel(artifact.kind)}</div>
                                        <div className="mt-1 text-[var(--text-secondary)]">{artifact.unavailable_reason || artifact.state}</div>
                                    </div>
                                ))}
                            </div>
                        </section>
                    ))}
                </div>
            </section>

            <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Alignment-session receipts</summary>
                <div className="mt-2 space-y-1 text-[var(--text-secondary)]">
                    {result.alignment_sessions.map((session) => (
                        <div key={session.session_id}>
                            <span className="font-mono">{session.session_id}</span> · {session.mode} · {session.ready ? 'ready' : session.unavailable_reason}
                        </div>
                    ))}
                </div>
            </details>

            <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Stage receipts</summary>
                <div className="mt-2 space-y-1 text-[var(--text-secondary)]">
                    {result.stages.map((stage) => (
                        <div key={stage.stage}>{stage.stage}: {stage.status} · {stage.output_count.toLocaleString()} governed outputs</div>
                    ))}
                </div>
            </details>

            <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Logs</summary>
                <div className="mt-2 space-y-1 text-[var(--text-secondary)]">
                    {logArtifacts.map((artifact) => artifact.state === 'present' ? (
                        <a key={artifact.display_order} href={artifact.url || undefined} download className="block text-sky-300 underline">
                            {artifact.display_order}. {metricLabel(artifact.kind)} · {artifact.source}
                        </a>
                    ) : (
                        <div key={artifact.display_order}>{artifact.display_order}. {metricLabel(artifact.kind)} · {artifact.unavailable_reason}</div>
                    ))}
                </div>
            </details>

            <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Technical provenance and historical resource fields</summary>
                <div className="mt-2 space-y-1 text-[var(--text-secondary)]">
                    <div>Sequence-QC manifest SHA-256: <span className="font-mono">{result.authority.sequence_qc_manifest_sha256}</span></div>
                    <div>Construct-verification manifest SHA-256: <span className="font-mono">{result.authority.construct_verification_manifest_sha256}</span></div>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-[10px]">{JSON.stringify(result.execution_resources, null, 2)}</pre>
                </div>
            </details>

            <details className="rounded border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3 text-xs">
                <summary className="cursor-pointer font-semibold text-[var(--text-primary)]">Technical authority and lifecycle</summary>
                <div className="mt-2 space-y-1 text-[var(--text-secondary)]">
                    <div>Stages: {result.stages.map((stage) => `${stage.stage}=${stage.status} (${stage.output_count})`).join(' · ')}</div>
                    <div>Threshold profile: {verification.threshold_profile.id} v{verification.threshold_profile.version} · {verification.threshold_profile.calibration_status}</div>
                    <div>Artifact set SHA-256: <span className="font-mono">{result.authority.artifact_set_sha256}</span></div>
                    <div>GPU: not applicable. This workflow consumes existing FASTQ and does not invoke Dorado.</div>
                </div>
            </details>
        </div>
    );
}
