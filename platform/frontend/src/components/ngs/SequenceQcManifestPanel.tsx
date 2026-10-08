import type { SequenceQcArtifact, SequenceQcManifest } from '../../lib/api';
import {
    sequenceQcManifestUnavailableLabel,
    type SequenceQcManifestStatus,
} from './sequenceQcManifestState';

interface SequenceQcManifestPanelProps {
    status: SequenceQcManifestStatus;
    manifest: SequenceQcManifest | null;
    message: string | null;
}

function countArtifacts(artifacts: SequenceQcArtifact[]) {
    return artifacts.reduce(
        (counts, artifact) => {
            const state = artifact.state || (artifact.exists ? 'present' : artifact.required ? 'missing_required' : 'missing_optional');
            if (state === 'present') counts.present += 1;
            else counts.unavailable += 1;
            return counts;
        },
        { present: 0, unavailable: 0 },
    );
}

function statusBadgeClass(status: SequenceQcManifestStatus): string {
    if (status === 'available') return 'bg-emerald-500/20 text-emerald-400';
    if (status === 'loading') return 'bg-blue-500/20 text-blue-300';
    if (status === 'malformed' || status === 'forbidden' || status === 'error') return 'bg-rose-500/20 text-rose-300';
    return 'bg-amber-500/20 text-amber-300';
}

export function SequenceQcManifestPanel({ status, manifest, message }: SequenceQcManifestPanelProps) {
    const artifacts = manifest?.artifacts || [];
    const artifactCounts = countArtifacts(artifacts);
    const fallbackConsensus = Boolean(manifest?.consensus?.fallback);
    const verifiedStatus = manifest?.interpretation?.verified_construct_status || 'review_required';

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Sequence-QC Manifest</h4>
                <span className={`text-[10px] px-2 py-0.5 rounded ${statusBadgeClass(status)}`}>
                    {status === 'available' ? 'available' : sequenceQcManifestUnavailableLabel(status)}
                </span>
            </div>
            <div className="bg-[var(--bg-tertiary)] rounded border border-[var(--border-primary)] p-3 text-sm">
                {status === 'idle' && (
                    <p className="text-[var(--text-secondary)]">Select a run to load its typed sequence-QC manifest.</p>
                )}
                {status === 'loading' && (
                    <p className="text-[var(--text-secondary)]">Loading sequence-QC manifest...</p>
                )}
                {status !== 'available' && status !== 'idle' && status !== 'loading' && (
                    <div className="space-y-1">
                        <p className="text-[var(--text-primary)]">{sequenceQcManifestUnavailableLabel(status)}</p>
                        {status === 'unavailable-old-run' && (
                            <p className="text-xs text-[var(--text-secondary)]">
                                manifest unavailable for older run: this looks like a legacy/older Nanopore run without qc_manifest.json. Treat existing path-scraped artifacts as legacy evidence, not a workflow failure.
                            </p>
                        )}
                        {message && <p className="text-xs text-[var(--text-secondary)] font-mono break-all">{message}</p>}
                    </div>
                )}
                {status === 'available' && manifest && (
                    <div className="space-y-2">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <div>
                                <div className="text-xs text-[var(--text-secondary)]">Schema</div>
                                <div className="text-[var(--text-primary)] font-mono">v{manifest.artifact_schema_version}</div>
                            </div>
                            <div>
                                <div className="text-xs text-[var(--text-secondary)]">Sample</div>
                                <div className="text-[var(--text-primary)] break-all">{manifest.sample_name || manifest.job_id}</div>
                            </div>
                            <div>
                                <div className="text-xs text-[var(--text-secondary)]">Artifacts</div>
                                <div className="text-[var(--text-primary)] font-mono">{artifactCounts.present}/{artifacts.length} present</div>
                            </div>
                            <div>
                                <div className="text-xs text-[var(--text-secondary)]">Verified status</div>
                                <div className="text-[var(--text-primary)] font-mono">{verifiedStatus}</div>
                            </div>
                        </div>
                        {fallbackConsensus && (
                            <p className="text-xs text-amber-300">
                                Fallback consensus cannot verify construct; inspect per-base/read evidence before accepting this sample.
                            </p>
                        )}
                        {artifactCounts.unavailable > 0 && (
                            <p className="text-xs text-[var(--text-secondary)]">
                                {artifactCounts.unavailable} manifest artifact(s) are not present or not applicable; absence is tracked explicitly instead of fabricating paths.
                            </p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
