import { useEffect, useMemo, useState } from 'react';
import {
    type ProtocolCompilePayload,
    type ProtocolJobSummary,
    useCompileProtocol,
    useExecuteProtocol,
    useProtocolJob,
    useProtocolJobs,
    useReviewProtocolJob,
} from '../lib/bioxpClient';
import { BioXpArtifactsPanel } from './BioXpArtifactsPanel';

const DEFAULT_REMOTE_XML_PATH = '/home/molbiofreak/bioxp_re/testdata/oem_xml/demo.xml';
const DEFAULT_REMOTE_LIFETEST_XML_PATH = '/home/molbiofreak/bioxp_re/testdata/oem_xml/lifetest.xml';
const DEFAULT_NATIVE_PROTOCOL = JSON.stringify(
    {
        protocol_id: 'bms-native-demo',
        stages: [
            {
                stage_id: 'stage-01',
                actions: [
                    {
                        action_id: 'note-01',
                        kind: 'note',
                        message: 'BioXP operator surface native demo',
                    },
                    {
                        action_id: 'pause-01',
                        kind: 'pause_review',
                        review_required: true,
                        pause_message: 'Confirm the deck state before continuing.',
                    },
                    {
                        action_id: 'note-02',
                        kind: 'note',
                        message: 'Protocol resumed after operator acknowledgement.',
                    },
                ],
            },
        ],
    },
    null,
    2,
);

const getErrorMessage = (error: unknown) => {
    if (error instanceof Error) {
        return error.message;
    }
    if (typeof error === 'string') {
        return error;
    }
    if (error && typeof error === 'object') {
        return JSON.stringify(error);
    }
    return 'Unknown error';
};

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

const statusBadgeClass = (status?: string) => {
    switch (status) {
        case 'completed':
            return 'bg-success/10 text-success border-success/30';
        case 'awaiting_review':
            return 'bg-warning/10 text-warning border-warning/30';
        case 'running':
            return 'bg-accent/10 text-accent border-accent/30';
        default:
            return 'bg-surface border-border-primary text-content-muted';
    }
};

const buildProtocolPayload = (
    sourceType: 'oem_xml' | 'native',
    xmlPath: string,
    nativeDocumentText: string,
): { payload: ProtocolCompilePayload | null; validationError: string | null } => {
    if (sourceType === 'oem_xml') {
        const trimmedPath = xmlPath.trim();
        if (!trimmedPath) {
            return {
                payload: null,
                validationError: 'Enter an OEM XML path on the robot filesystem before compiling or executing.',
            };
        }
        return {
            payload: {
                source_type: 'oem_xml',
                xml_path: trimmedPath,
            },
            validationError: null,
        };
    }

    const trimmedDocument = nativeDocumentText.trim();
    if (!trimmedDocument) {
        return {
            payload: null,
            validationError: 'Enter a native protocol JSON document before compiling or executing.',
        };
    }

    try {
        const parsedDocument = JSON.parse(trimmedDocument) as Record<string, any>;
        return {
            payload: {
                source_type: 'native',
                document: parsedDocument,
            },
            validationError: null,
        };
    } catch (error) {
        return {
            payload: null,
            validationError: `Native protocol JSON is invalid: ${getErrorMessage(error)}`,
        };
    }
};

const ProtocolJobRow = ({
    row,
    selected,
    onSelect,
}: {
    row: ProtocolJobSummary;
    selected: boolean;
    onSelect: (jobId: string) => void;
}) => {
    const pendingReview = row.pending_review;
    return (
        <button
            onClick={() => onSelect(row.job_id)}
            className={`w-full text-left p-3 rounded-lg border transition-colors ${selected ? 'border-accent bg-accent/5' : 'border-border-primary bg-surface hover:border-accent/40'}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-content">{row.protocol_id ?? row.job_id}</div>
                    <div className="text-[11px] font-mono text-content-muted break-all">{row.job_id}</div>
                </div>
                <div className={`px-2 py-1 rounded-sm text-[10px] font-mono font-semibold border ${statusBadgeClass(row.status)}`}>
                    {row.status ?? 'unknown'}
                </div>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-content-muted">
                <div>Source: {row.source_type ?? '—'}</div>
                <div>{row.dry_run ? 'Dry run' : 'Live run'}</div>
                <div>Created: {formatTimestamp(row.created_at)}</div>
                <div>Updated: {formatTimestamp(row.updated_at)}</div>
            </div>
            {pendingReview ? (
                <div className="mt-2 text-[11px] text-warning">
                    Awaiting review at {String(pendingReview.stage_id ?? 'unknown stage')} / {String(pendingReview.action_id ?? 'unknown action')}
                </div>
            ) : null}
        </button>
    );
};

export const BioXpProtocolRunner = ({
    linkageConfigured,
    daemonState,
    daemonStatusHelp,
}: {
    linkageConfigured: boolean;
    daemonState?: string;
    daemonStatusHelp?: string | null;
}) => {
    const [sourceType, setSourceType] = useState<'oem_xml' | 'native'>('oem_xml');
    const [xmlPath, setXmlPath] = useState(DEFAULT_REMOTE_XML_PATH);
    const [nativeDocumentText, setNativeDocumentText] = useState(DEFAULT_NATIVE_PROTOCOL);
    const [dryRun, setDryRun] = useState(true);
    const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
    const [reviewNote, setReviewNote] = useState('');

    const protocolJobs = useProtocolJobs(linkageConfigured, linkageConfigured ? 10000 : false);
    const protocolJob = useProtocolJob(selectedJobId, linkageConfigured && !!selectedJobId);
    const compileProtocol = useCompileProtocol();
    const executeProtocol = useExecuteProtocol();
    const reviewProtocolJob = useReviewProtocolJob();

    const payloadState = useMemo(
        () => buildProtocolPayload(sourceType, xmlPath, nativeDocumentText),
        [sourceType, xmlPath, nativeDocumentText],
    );

    const jobRows = protocolJobs.data?.rows ?? [];
    const selectedSummary = jobRows.find((row) => row.job_id === selectedJobId) ?? null;
    const selectedJob = protocolJob.data ?? null;
    const compilePreview = compileProtocol.data ?? null;
    const pendingReview = selectedJob?.operator?.pending_review ?? selectedSummary?.pending_review ?? null;
    const protocolDocument = selectedJob?.protocol.document ?? compilePreview?.document ?? null;
    const coverage = selectedJob?.protocol.coverage ?? compilePreview?.coverage ?? null;
    const runtimeState = selectedJob?.execution.runtime_state ?? null;

    useEffect(() => {
        if (!jobRows.length) {
            if (selectedJobId !== null) {
                setSelectedJobId(null);
            }
            return;
        }
        if (!selectedJobId || !jobRows.some((row) => row.job_id === selectedJobId)) {
            setSelectedJobId(jobRows[0].job_id);
        }
    }, [jobRows, selectedJobId]);

    const submitDisabled = !linkageConfigured || !payloadState.payload || executeProtocol.isPending;
    const compileDisabled = !linkageConfigured || !payloadState.payload || compileProtocol.isPending;

    const handleCompile = () => {
        if (!payloadState.payload) {
            return;
        }
        compileProtocol.mutate(payloadState.payload);
    };

    const handleExecute = () => {
        if (!payloadState.payload) {
            return;
        }
        executeProtocol.mutate(
            {
                ...payloadState.payload,
                dry_run: dryRun,
            },
            {
                onSuccess: (bundle) => {
                    setSelectedJobId(bundle.job_id);
                    setReviewNote('');
                },
            },
        );
    };

    const handleReview = () => {
        if (!selectedJobId) {
            return;
        }
        reviewProtocolJob.mutate(
            {
                job_id: selectedJobId,
                reviewer: 'bms-operator',
                note: reviewNote.trim() || null,
            },
            {
                onSuccess: () => {
                    setReviewNote('');
                },
            },
        );
    };

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                <div className="xl:col-span-2 p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
                    <div className="space-y-1 border-b border-border-secondary pb-2">
                        <h3 className="text-sm font-semibold text-content">Protocol Submit / Compile / Resume</h3>
                        <p className="text-xs text-content-muted">
                            Operator-facing semantic surface for compiling OEM XML or native protocol JSON, launching protocol jobs, and acknowledging manual review gates.
                        </p>
                    </div>

                    <div className="flex flex-wrap gap-2 items-center">
                        <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${linkageConfigured ? 'bg-success/10 text-success border-success/30' : 'bg-error/10 text-error border-error/30'}`}>
                            LINKAGE: {linkageConfigured ? 'CONFIGURED' : 'MISSING'}
                        </div>
                        <div className={`px-3 py-1.5 rounded-sm text-xs font-mono font-semibold border ${daemonState === 'running' || daemonState === 'inferred' || daemonState === 'proxy-running' ? 'bg-success/10 text-success border-success/30' : daemonState === 'stopped' ? 'bg-error/10 text-error border-error/30' : 'bg-warning/10 text-warning border-warning/30'}`}>
                            DAEMON: {(daemonState ?? 'unknown').toUpperCase()}
                        </div>
                    </div>
                    {daemonStatusHelp ? <div className="text-xs text-content-muted">{daemonStatusHelp}</div> : null}

                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => setSourceType('oem_xml')}
                            className={`px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${sourceType === 'oem_xml' ? 'bg-accent text-white' : 'bg-surface border border-border-primary text-content hover:border-accent/40'}`}
                        >
                            OEM XML Import
                        </button>
                        <button
                            onClick={() => setSourceType('native')}
                            className={`px-3 py-2 rounded-lg text-xs font-semibold transition-colors ${sourceType === 'native' ? 'bg-accent text-white' : 'bg-surface border border-border-primary text-content hover:border-accent/40'}`}
                        >
                            Native JSON
                        </button>
                    </div>

                    {sourceType === 'oem_xml' ? (
                        <div className="space-y-3">
                            <div>
                                <div className="text-xs font-semibold text-content-muted mb-2">Robot XML Path</div>
                                <input
                                    type="text"
                                    value={xmlPath}
                                    onChange={(event) => setXmlPath(event.target.value)}
                                    placeholder="/home/molbiofreak/bioxp_re/testdata/oem_xml/demo.xml"
                                    className="w-full bg-surface border border-accent/20 rounded-lg px-3 py-2 text-content text-sm font-mono"
                                />
                            </div>
                            <div className="flex flex-wrap gap-2 text-xs">
                                <button
                                    onClick={() => setXmlPath(DEFAULT_REMOTE_XML_PATH)}
                                    className="px-3 py-1.5 bg-surface border border-border-primary text-content rounded-lg hover:border-accent/40"
                                >
                                    Use Demo Fixture
                                </button>
                                <button
                                    onClick={() => setXmlPath(DEFAULT_REMOTE_LIFETEST_XML_PATH)}
                                    className="px-3 py-1.5 bg-surface border border-border-primary text-content rounded-lg hover:border-accent/40"
                                >
                                    Use Lifetest Fixture
                                </button>
                            </div>
                            <div className="text-[11px] text-content-muted">
                                XML paths are resolved on the robot daemon host, not on the browser machine.
                            </div>
                        </div>
                    ) : (
                        <div className="space-y-2">
                            <div className="text-xs font-semibold text-content-muted">Native Protocol JSON</div>
                            <textarea
                                value={nativeDocumentText}
                                onChange={(event) => setNativeDocumentText(event.target.value)}
                                rows={16}
                                className="w-full bg-[#000000] border border-border-primary rounded-lg px-3 py-2 text-content text-xs font-mono"
                            />
                        </div>
                    )}

                    <label className="flex items-center gap-2 text-xs text-content-muted">
                        <input
                            type="checkbox"
                            checked={dryRun}
                            onChange={(event) => setDryRun(event.target.checked)}
                            className="rounded border-border-primary"
                        />
                        Dry run only (recommended unless you are intentionally exercising a live protocol path)
                    </label>

                    {payloadState.validationError ? (
                        <div className="text-xs text-error">{payloadState.validationError}</div>
                    ) : null}
                    {!linkageConfigured ? (
                        <div className="text-xs text-error">Configure a BioXP daemon linkage first. The operator surface delegates to the robot-local protocol API.</div>
                    ) : null}
                    {(compileProtocol.isError || executeProtocol.isError || reviewProtocolJob.isError) ? (
                        <div className="text-xs text-error">
                            {compileProtocol.isError
                                ? getErrorMessage(compileProtocol.error)
                                : executeProtocol.isError
                                    ? getErrorMessage(executeProtocol.error)
                                    : getErrorMessage(reviewProtocolJob.error)}
                        </div>
                    ) : null}

                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={handleCompile}
                            disabled={compileDisabled}
                            className="px-4 py-2 bg-accent/20 hover:bg-accent/30 text-accent text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {compileProtocol.isPending ? 'COMPILING...' : 'Compile / Preview'}
                        </button>
                        <button
                            onClick={handleExecute}
                            disabled={submitDisabled}
                            className="px-4 py-2 bg-accent hover:bg-accent/80 text-white text-xs rounded-lg transition-colors disabled:opacity-40"
                        >
                            {executeProtocol.isPending ? 'SUBMITTING...' : dryRun ? 'Run Dry-Run Job' : 'Run Live Job'}
                        </button>
                        {pendingReview ? (
                            <button
                                onClick={handleReview}
                                disabled={reviewProtocolJob.isPending || !selectedJobId}
                                className="px-4 py-2 bg-warning/20 hover:bg-warning/30 text-warning text-xs rounded-lg transition-colors disabled:opacity-40"
                            >
                                {reviewProtocolJob.isPending ? 'RESUMING...' : 'Approve Review Gate & Resume'}
                            </button>
                        ) : null}
                    </div>
                </div>

                <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
                    <div className="space-y-1 border-b border-border-secondary pb-2">
                        <h3 className="text-sm font-semibold text-content">Selected Job State</h3>
                        <p className="text-xs text-content-muted">Protocol status, pending review markers, and runtime position.</p>
                    </div>

                    <div className="space-y-2 text-xs text-content-muted">
                        <div className="flex justify-between gap-3">
                            <span>Status</span>
                            <span className="font-semibold text-content">{selectedJob?.status ?? selectedSummary?.status ?? '—'}</span>
                        </div>
                        <div className="flex justify-between gap-3">
                            <span>Protocol</span>
                            <span className="font-semibold text-content text-right break-all">{String(selectedJob?.protocol.document.protocol_id ?? selectedSummary?.protocol_id ?? '—')}</span>
                        </div>
                        <div className="flex justify-between gap-3">
                            <span>Dry Run</span>
                            <span className="font-semibold text-content">{(selectedJob?.execution.dry_run ?? selectedSummary?.dry_run) ? 'Yes' : 'No'}</span>
                        </div>
                        <div className="flex justify-between gap-3">
                            <span>Current Stage</span>
                            <span className="font-semibold text-content text-right break-all">{String(runtimeState?.current_stage_id ?? pendingReview?.stage_id ?? '—')}</span>
                        </div>
                    </div>

                    {pendingReview ? (
                        <div className="p-3 bg-warning/5 border border-warning/20 rounded-lg space-y-2">
                            <div className="text-xs font-semibold text-warning">Manual review required</div>
                            <div className="text-xs text-content-muted">
                                Stage {String(pendingReview.stage_id ?? 'unknown')} / action {String(pendingReview.action_id ?? 'unknown')}
                            </div>
                            <textarea
                                value={reviewNote}
                                onChange={(event) => setReviewNote(event.target.value)}
                                rows={4}
                                placeholder="Optional operator review note"
                                className="w-full bg-surface border border-warning/20 rounded-lg px-3 py-2 text-content text-xs"
                            />
                        </div>
                    ) : (
                        <div className="text-xs text-content-muted">No review gate is currently pending for the selected job.</div>
                    )}

                    <div className="space-y-2">
                        <div className="text-xs font-semibold text-content-muted">Compile Preview / Selected Document</div>
                        <pre className="text-[10px] font-mono text-content-muted p-3 bg-[#000000] rounded border border-border-primary overflow-x-auto max-h-80">
                            {protocolDocument ? JSON.stringify(protocolDocument, null, 2) : 'Compile a source or select a job to inspect the normalized protocol document.'}
                        </pre>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_1.4fr] gap-6">
                <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-4">
                    <div className="space-y-1 border-b border-border-secondary pb-2">
                        <h3 className="text-sm font-semibold text-content">Protocol Jobs</h3>
                        <p className="text-xs text-content-muted">Latest persisted protocol runs and review-gated bundles.</p>
                    </div>
                    {protocolJobs.isLoading ? (
                        <div className="text-xs text-content-muted">Loading protocol jobs…</div>
                    ) : jobRows.length ? (
                        <div className="space-y-2 max-h-[38rem] overflow-y-auto pr-1">
                            {jobRows.map((row) => (
                                <ProtocolJobRow
                                    key={row.job_id}
                                    row={row}
                                    selected={row.job_id === selectedJobId}
                                    onSelect={setSelectedJobId}
                                />
                            ))}
                        </div>
                    ) : (
                        <div className="text-xs text-content-muted">No protocol jobs have been persisted yet. Run a compile/execute cycle to seed the operator bundle store.</div>
                    )}
                </div>

                <div className="space-y-6">
                    <BioXpArtifactsPanel job={selectedJob} />
                    <div className="p-4 bg-surface-secondary border border-border-primary rounded-lg space-y-3">
                        <div>
                            <h3 className="text-sm font-semibold text-content">Coverage / Runtime JSON</h3>
                            <p className="text-xs text-content-muted">Structured evidence behind the semantic operator surface.</p>
                        </div>
                        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                            <pre className="text-[10px] font-mono text-content-muted p-3 bg-[#000000] rounded border border-border-primary overflow-x-auto max-h-80">
                                {coverage ? JSON.stringify(coverage, null, 2) : 'No coverage metadata available.'}
                            </pre>
                            <pre className="text-[10px] font-mono text-content-muted p-3 bg-[#000000] rounded border border-border-primary overflow-x-auto max-h-80">
                                {runtimeState ? JSON.stringify(runtimeState, null, 2) : 'No runtime state available.'}
                            </pre>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
