import { useState } from 'react';
import { ProjectAttachmentDialog } from '../project-manager/ProjectAttachmentDialog';
import type {
    ConstructVerificationInputEvidence,
    SequenceQcArtifact,
    SequenceQcManifest,
} from '../../lib/api';
import {
    sequenceQcManifestUnavailableLabel,
    type SequenceQcManifestStatus,
} from './sequenceQcManifestState';

interface SequenceQcManifestPanelProps {
    status: SequenceQcManifestStatus;
    manifest: SequenceQcManifest | null;
    message: string | null;
    onNavigateLocus?: (position_1based: number, end_1based: number | undefined, source: string) => void;
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
    if (status === 'malformed' || status === 'access-denied' || status === 'forbidden' || status === 'error') return 'bg-rose-500/20 text-rose-300';
    return 'bg-amber-500/20 text-amber-300';
}

function verificationVerdictClass(verdict: string): string {
    if (verdict === 'PASS') return 'border-emerald-500/50 bg-emerald-500/15 text-emerald-300';
    if (verdict === 'FAIL') return 'border-rose-500/50 bg-rose-500/15 text-rose-300';
    return 'border-amber-500/50 bg-amber-500/15 text-amber-300';
}

const VERIFICATION_CHECK_LABELS: Record<string, string> = {
    sequence_identity: 'Sequence identity',
    read_support: 'Read support',
    coverage: 'Coverage',
    contamination: 'Contamination screen',
    topology: 'Topology',
};

function formatMetric(value: unknown): string {
    if (value === null || value === undefined) return 'not reported';
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toPrecision(5);
    if (typeof value === 'boolean' || typeof value === 'string') return String(value);
    return JSON.stringify(value);
}

function evidenceDigest(evidence: ConstructVerificationInputEvidence | undefined): string {
    return evidence?.normalized_sequence_sha256
        || evidence?.declared_sequence_sha256
        || evidence?.sha256
        || 'unavailable';
}

function checkPosition(metrics: Record<string, unknown>): number | null {
    for (const key of ['position_1based', 'position', 'start_1based']) {
        const value = metrics[key];
        if (typeof value === 'number' && Number.isInteger(value) && value >= 1) return value;
    }
    return null;
}

function EvidenceCard({ label, evidence }: { label: string; evidence: ConstructVerificationInputEvidence | undefined }) {
    return (
        <div className="rounded border border-[var(--border-primary)] p-2 text-[10px]">
            <div className="text-xs font-semibold text-[var(--text-primary)]">{label}</div>
            <div>Role: <code>{evidence?.role || 'unavailable'}</code></div>
            <div>State: <code>{evidence?.state || 'unavailable'}</code></div>
            <div>Digest: <code className="break-all">{evidenceDigest(evidence)}</code></div>
            <div>Independent from expected: <code>{String(evidence?.independent_from_expected ?? 'unavailable')}</code></div>
            <div>Validation: <code>{evidence?.semantic_validation?.status || 'unavailable'}</code></div>
            <div>Validator: <code>{evidence?.semantic_validation?.validator || 'unavailable'}</code></div>
            {(evidence?.reason || evidence?.semantic_validation?.reason) && (
                <div className="mt-1 break-all text-amber-300">{evidence.reason || evidence.semantic_validation?.reason}</div>
            )}
        </div>
    );
}

export function SequenceQcManifestPanel({ status, manifest, message, onNavigateLocus }: SequenceQcManifestPanelProps) {
    const [addToProjectOpen, setAddToProjectOpen] = useState(false);
    const artifacts = manifest?.artifacts || [];
    const artifactCounts = countArtifacts(artifacts);
    const isConstructVerification = manifest?.schema === 'biomodstack.construct_verification.v2';
    const verificationChecks = isConstructVerification && manifest ? (manifest.checks || {}) : {};
    const verificationVariants = isConstructVerification && manifest ? (manifest.variants || []) : [];
    const fallbackConsensus = Boolean(manifest?.consensus?.fallback);
    const verifiedStatus = manifest?.interpretation?.verified_construct_status || 'review_required';
    const expectedEvidence = manifest?.inputs?.reference;
    const observedEvidence = manifest?.inputs?.observed;

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Sequence-QC Manifest</h4>
                <div className="flex items-center gap-2">
                    {status === 'available' && manifest?.job_id && (
                        <button type="button" onClick={() => setAddToProjectOpen(true)} className="rounded border border-[var(--accent-primary)]/50 px-2 py-1 text-[10px] font-semibold text-[var(--accent-primary)]">Add to Project / Experiment</button>
                    )}
                    <span className={`text-[10px] px-2 py-0.5 rounded ${statusBadgeClass(status)}`}>
                        {status === 'available' ? 'available' : sequenceQcManifestUnavailableLabel(status)}
                    </span>
                </div>
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
                    <details>
                        <summary className="cursor-pointer text-xs font-semibold text-[var(--text-primary)]">Sequence-QC and construct-verification manifest details</summary>
                        <div className="mt-3 space-y-3">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <div>
                                <div className="text-xs text-[var(--text-secondary)]">Schema</div>
                                <div className="text-[var(--text-primary)] font-mono break-all">{manifest.schema || `v${manifest.artifact_schema_version}`}</div>
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
                                <div className="text-xs text-[var(--text-secondary)]">Threshold profile</div>
                                <div className="text-[var(--text-primary)] font-mono break-all">
                                    {manifest.threshold_profile
                                        ? `${manifest.threshold_profile.id} v${manifest.threshold_profile.version} · ${manifest.threshold_profile.sha256 || 'digest unavailable'}`
                                        : 'not reported'}
                                </div>
                            </div>
                        </div>
                        {isConstructVerification ? (
                            <>
                                <div className="rounded border border-[var(--border-primary)] p-2 text-[10px]">
                                    <div className="mb-1 text-xs font-semibold text-[var(--text-primary)]">Verification provenance</div>
                                    {Object.entries(manifest.provenance || {}).length === 0 ? (
                                        <div className="text-amber-300">No top-level provenance reported.</div>
                                    ) : Object.entries(manifest.provenance || {}).map(([key, value]) => (
                                        <div key={key} className="grid grid-cols-[minmax(8rem,0.35fr)_1fr] gap-2">
                                            <span className="text-[var(--text-secondary)] break-all">{key}</span>
                                            <code className="break-all">{formatMetric(value)}</code>
                                        </div>
                                    ))}
                                </div>
                                <div className={`rounded border p-3 ${verificationVerdictClass(manifest.verdict || 'REVIEW')}`}>
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-xs uppercase tracking-wide">Construct verification</span>
                                        <strong className="text-xl font-mono">{manifest.verdict || 'REVIEW'}</strong>
                                    </div>
                                    <div className="mt-2 flex flex-wrap gap-3 text-[10px]">
                                        <span>Execution: <strong>{manifest.execution?.status || 'UNKNOWN'}</strong></span>
                                        <span>Calibration: <strong>{manifest.threshold_profile?.calibration_status || 'unreported'}</strong></span>
                                        <span>Public accuracy validated: <strong>{manifest.threshold_profile?.public_accuracy_validated ? 'yes' : 'no'}</strong></span>
                                    </div>
                                    {!manifest.threshold_profile?.public_accuracy_validated && (
                                        <p className="mt-2 text-[10px]">Experimental thresholds: verdict is not a public-data biological-accuracy claim.</p>
                                    )}
                                    <div className="mt-2 flex flex-wrap gap-1">
                                        {(manifest.reason_codes || ['MALFORMED_VERIFICATION_MANIFEST']).map((code) => (
                                            <code key={code} className="rounded bg-black/20 px-1.5 py-0.5 text-[10px]">{code}</code>
                                        ))}
                                    </div>
                                </div>
                                <div className="grid gap-2 md:grid-cols-2">
                                    <EvidenceCard label="Expected reference" evidence={expectedEvidence} />
                                    <EvidenceCard label="Observed evidence" evidence={observedEvidence} />
                                </div>
                                <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-5">
                                    {Object.entries(verificationChecks).map(([name, check]) => (
                                        <div key={name} className="rounded border border-[var(--border-primary)] p-2">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="text-xs text-[var(--text-primary)]">{VERIFICATION_CHECK_LABELS[name] || name}</span>
                                                <code className="text-[10px] uppercase">{check.status}</code>
                                            </div>
                                            {Object.entries(check.metrics || {}).slice(0, 4).map(([metric, value]) => (
                                                <div key={metric} className="mt-1 flex justify-between gap-2 text-[10px]">
                                                    <span className="text-[var(--text-secondary)] break-all">{metric}</span>
                                                    <span className="font-mono text-right break-all">{formatMetric(value)}</span>
                                                </div>
                                            ))}
                                            {check.reason_codes.length > 0 && (
                                                <div className="mt-1 text-[10px] text-amber-300 break-all">{check.reason_codes.join(', ')}</div>
                                            )}
                                            {check.status !== 'pass' && checkPosition(check.metrics || {}) !== null && onNavigateLocus && (
                                                <button
                                                    type="button"
                                                    className="mt-1 rounded border border-[var(--border-primary)] px-1.5 py-0.5 text-[10px]"
                                                    onClick={() => onNavigateLocus(checkPosition(check.metrics || {})!, undefined, `check:${name}`)}
                                                >
                                                    View failed check in selected IGV session
                                                </button>
                                            )}
                                        </div>
                                    ))}
                                </div>
                                <div>
                                    <div className="mb-1 text-xs text-[var(--text-secondary)]">Normalized variants ({verificationVariants.length})</div>
                                    {verificationVariants.length === 0 ? (
                                        <p className="text-xs text-[var(--text-primary)]">No sequence differences reported.</p>
                                    ) : (
                                        <div className="max-h-40 overflow-auto rounded border border-[var(--border-primary)]">
                                            <table className="w-full text-xs">
                                                <thead><tr><th className="p-1 text-left">Type</th><th className="p-1 text-left">Position</th><th className="p-1 text-left">Ref</th><th className="p-1 text-left">Alt</th><th className="p-1 text-left">Support</th><th className="p-1 text-left">Depth</th><th className="p-1 text-left">Fraction</th><th className="p-1 text-left">Origin</th></tr></thead>
                                                <tbody>
                                                    {verificationVariants.map((variant, index) => (
                                                        <tr key={`${variant.id || variant.kind || 'variant'}-${variant.position_1based || index}`} className="border-t border-[var(--border-primary)]">
                                                            <td className="p-1 font-mono">{variant.kind || 'unknown'}</td>
                                                            <td className="p-1 font-mono">
                                                                {variant.position_1based && onNavigateLocus ? (
                                                                    <button
                                                                        type="button"
                                                                        className="underline decoration-dotted underline-offset-2"
                                                                        onClick={() => onNavigateLocus(
                                                                            variant.position_1based!,
                                                                            variant.end_1based,
                                                                            `variant:${variant.id || index}`,
                                                                        )}
                                                                    >
                                                                        {variant.position_1based}
                                                                    </button>
                                                                ) : (variant.position_1based ?? '—')}
                                                            </td>
                                                            <td className="p-1 font-mono break-all">{variant.ref || '—'}</td>
                                                            <td className="p-1 font-mono break-all">{variant.alt || '—'}</td>
                                                            <td className="p-1 font-mono">{variant.support_status || 'not_evaluated'}</td>
                                                            <td className="p-1 font-mono">{variant.depth ?? '—'}</td>
                                                            <td className="p-1 font-mono">{variant.support_fraction == null ? '—' : variant.support_fraction.toPrecision(5)}</td>
                                                            <td className="p-1">{variant.circular_event_id ? 'spanning' : 'no'}</td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </div>
                            </>
                        ) : (
                            <div>
                                <div className="text-xs text-[var(--text-secondary)]">Verified status</div>
                                <div className="text-[var(--text-primary)] font-mono">{verifiedStatus}</div>
                            </div>
                        )}
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
                    </details>
                )}
            </div>
            <ProjectAttachmentDialog
                open={addToProjectOpen && Boolean(manifest?.job_id)}
                source={{ adapterId: 'bms.ngs.sequence-qc-reference.adapter.v1', entityId: manifest?.job_id ?? '', label: manifest?.sample_name || manifest?.job_id || 'Sequence-QC manifest', availability: 'available' }}
                onClose={() => setAddToProjectOpen(false)}
            />
        </div>
    );
}
