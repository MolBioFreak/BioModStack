import type { ProtocolJobBundle } from '../lib/bioxpClient';

const formatTimestamp = (value?: string) => {
    if (!value) {
        return '—';
    }
    const parsed = Date.parse(value);
    if (Number.isNaN(parsed)) {
        return value;
    }
    return new Date(parsed).toLocaleString();
};

const formatScalar = (value: unknown) => {
    if (value == null) {
        return '—';
    }
    if (typeof value === 'string') {
        return value;
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
    }
    return JSON.stringify(value);
};

export const BioXpArtifactsPanel = ({ job }: { job: ProtocolJobBundle | null }) => {
    if (!job) {
        return (
            <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg text-sm text-content-muted">
                Select a protocol job to inspect its persisted bundle, evidence paths, and execution metadata.
            </div>
        );
    }

    const coverage = job.protocol.coverage ?? {};
    const runtimeState = job.execution.runtime_state ?? {};
    const artifacts = job.artifacts ?? {};
    const recentEvents = Array.isArray(runtimeState.events)
        ? runtimeState.events.slice(-6).reverse()
        : [];
    const pathEntries = [
        { label: 'Job Directory', value: artifacts.job_dir },
        { label: 'Bundle JSON', value: artifacts.bundle_path },
        { label: 'Source Path', value: job.protocol.source_path },
    ].filter((entry): entry is { label: string; value: string } => typeof entry.value === 'string' && entry.value.length > 0);

    const metadataEntries = [
        { label: 'Schema', value: job.schema_version },
        { label: 'Status', value: job.status },
        { label: 'Dry Run', value: job.execution.dry_run },
        { label: 'Created', value: formatTimestamp(job.created_at) },
        { label: 'Updated', value: formatTimestamp(job.updated_at) },
        { label: 'Protocol ID', value: job.protocol.document.protocol_id },
        { label: 'Source Type', value: job.protocol.source_type },
        { label: 'Current Stage', value: runtimeState.current_stage_id },
    ];

    const coverageEntries = [
        { label: 'Command Nodes', value: coverage.command_nodes_total },
        { label: 'Supported Commands', value: coverage.supported_command_count },
        { label: 'Unsupported Commands', value: coverage.unsupported_command_count },
        { label: 'Coverage Ratio', value: coverage.coverage_ratio },
    ].filter((entry) => entry.value != null);

    return (
        <div className="space-y-4">
            <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
                <div className="space-y-1 border-b border-border-secondary pb-2">
                    <h3 className="text-sm font-semibold text-content">Persisted Operator Artifacts</h3>
                    <p className="text-xs text-content-muted">
                        This is the durable operator bundle written by the BioXP runtime for the selected protocol job.
                    </p>
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <div className="text-xs font-semibold text-content-muted">Artifact Paths</div>
                        {pathEntries.length ? (
                            <div className="space-y-2">
                                {pathEntries.map((entry) => (
                                    <div key={entry.label} className="p-3 bg-surface border border-border-primary rounded-lg">
                                        <div className="text-[11px] uppercase tracking-wide text-content-muted">{entry.label}</div>
                                        <div className="text-xs font-mono text-accent break-all">{entry.value}</div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="text-xs text-content-muted">No filesystem artifacts were recorded on this job yet.</div>
                        )}
                    </div>

                    <div className="space-y-2">
                        <div className="text-xs font-semibold text-content-muted">Bundle Metadata</div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            {metadataEntries.map((entry) => (
                                <div key={entry.label} className="p-3 bg-surface border border-border-primary rounded-lg">
                                    <div className="text-[11px] uppercase tracking-wide text-content-muted">{entry.label}</div>
                                    <div className="text-xs text-content break-all">{formatScalar(entry.value)}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-3">
                    <div>
                        <h3 className="text-sm font-semibold text-content">Coverage Summary</h3>
                        <p className="text-xs text-content-muted">Importer coverage and unsupported-verb accounting for the selected protocol source.</p>
                    </div>
                    {coverageEntries.length ? (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                            {coverageEntries.map((entry) => (
                                <div key={entry.label} className="p-3 bg-surface border border-border-primary rounded-lg">
                                    <div className="text-[11px] uppercase tracking-wide text-content-muted">{entry.label}</div>
                                    <div className="text-xs text-content">{formatScalar(entry.value)}</div>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-xs text-content-muted">This job does not include importer coverage metadata.</div>
                    )}
                    {coverage.unsupported_verbs ? (
                        <pre className="text-[10px] font-mono text-content-muted p-3 bg-[#000000] rounded border border-border-primary overflow-x-auto max-h-48">
                            {JSON.stringify(coverage.unsupported_verbs, null, 2)}
                        </pre>
                    ) : null}
                </div>

                <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-3">
                    <div>
                        <h3 className="text-sm font-semibold text-content">Recent Runtime Events</h3>
                        <p className="text-xs text-content-muted">Latest protocol state transitions captured in the runtime bundle.</p>
                    </div>
                    {recentEvents.length ? (
                        <div className="space-y-2">
                            {recentEvents.map((event, index) => (
                                <div key={`${event.event ?? 'event'}-${index}`} className="p-3 bg-surface border border-border-primary rounded-lg">
                                    <div className="flex justify-between gap-3 text-[11px] text-content-muted uppercase tracking-wide">
                                        <span>{formatScalar(event.event)}</span>
                                        <span>{formatTimestamp(typeof event.timestamp === 'string' ? event.timestamp : undefined)}</span>
                                    </div>
                                    <pre className="text-[10px] font-mono text-content-muted mt-2 overflow-x-auto">
                                        {JSON.stringify(event, null, 2)}
                                    </pre>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-xs text-content-muted">No runtime events were persisted for this job.</div>
                    )}
                </div>
            </div>
        </div>
    );
};
