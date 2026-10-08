import { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { withAlignmentAccessRecovery, describeNgsError } from '../../lib/ngsAlignmentSession';
import { AlignmentDerivedStatus } from './AlignmentDerivedStatus';
import type { CatalogSignalSelection, CatalogReadAction } from './CatalogReadTable';

interface Discovery {
    native: boolean;
    sessions: Array<{ session_id: string; source_authority_sha256: string; reference: { contig: string } }>;
    unavailable_reason: 'unsupported_source' | 'source_invalid' | null;
}
/** Native receipts have no invented sequence-QC or verification manifests. */
export function NativeCatalogDiscovery({ jobId, fallbackSessionId, signal, onReadAction, onSelectionIntent, selectedReadId }: { jobId: string; fallbackSessionId?: string; signal?: CatalogSignalSelection; onReadAction?: CatalogReadAction; onSelectionIntent?: () => (() => boolean); selectedReadId?: string | null }) {
    const [value, setValue] = useState<Discovery | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const [artifacts, setArtifacts] = useState<Array<{ artifact_id: string; filename: string; size_bytes: number }>>([]);
    const [artifactError, setArtifactError] = useState<string | null>(null);
    const [downloadBusy, setDownloadBusy] = useState<string | null>(null);
    const downloadController = useRef<AbortController | null>(null);
    useEffect(() => {
        const controller = new AbortController(); setArtifacts([]); setArtifactError(null); setDownloadBusy(null);
        downloadController.current?.abort();
        if (!value?.native) return () => controller.abort();
        void withAlignmentAccessRecovery(jobId, () => api.get(`/api/jobs/${encodeURIComponent(jobId)}/ngs-artifacts`, { signal: controller.signal }))
            .then(({ data }) => {
                if (controller.signal.aborted) return;
                if (data?.job_id !== jobId || !Array.isArray(data.artifacts) || data.artifacts.some((row: { artifact_id?: unknown; filename?: unknown; size_bytes?: unknown }) =>
                    typeof row.artifact_id !== 'string' || !/^[0-9a-f]{64}$/.test(row.artifact_id) || typeof row.filename !== 'string'
                    || typeof row.size_bytes !== 'number' || !Number.isSafeInteger(row.size_bytes) || row.size_bytes < 0)) throw new Error('Invalid native artifact inventory.');
                setArtifacts(data.artifacts);
            }).catch((reason: unknown) => { if (!controller.signal.aborted) setArtifactError(describeNgsError(reason, 'Native artifacts are unavailable.')); });
        return () => { controller.abort(); downloadController.current?.abort(); };
    }, [jobId, value?.native, revision]);
    const download = async (artifactId: string, retryCache = false) => {
        if (downloadBusy) return;
        setDownloadBusy(artifactId); setArtifactError(null);
        const controller = new AbortController(); downloadController.current = controller;
        const url = `/api/jobs/${encodeURIComponent(jobId)}/ngs-artifacts/${encodeURIComponent(artifactId)}`;
        try {
            if (retryCache) {
                // Explicit mutation: never replay after capability recovery.
                await api.post(`${url}/cache/retry`, undefined, { signal: controller.signal });
                return;
            }
            await withAlignmentAccessRecovery(jobId, () => api.head(url, { signal: controller.signal }));
            if (controller.signal.aborted) return;
            const link = document.createElement('a'); link.href = url; link.download = ''; link.click();
        } catch (reason) { if (!controller.signal.aborted) setArtifactError(describeNgsError(reason, 'Download is unavailable.')); }
        finally { if (!controller.signal.aborted) setDownloadBusy(null); }
    };
    useEffect(() => {
        const controller = new AbortController(); setValue(null); setError(null);
        void withAlignmentAccessRecovery(jobId, () => api.get(`/api/jobs/${encodeURIComponent(jobId)}/catalog-sessions`, { signal: controller.signal }))
            .then(({ data }) => {
                if (controller.signal.aborted) return;
                if (data?.schema !== 'bms.ngs.native-catalog-sessions.v2' || data.job_id !== jobId
                    || typeof data.native !== 'boolean' || !Array.isArray(data.sessions)
                    || ![null, 'unsupported_source', 'source_invalid'].includes(data.unavailable_reason)
                    || data.sessions.some((row: { session_id?: unknown; source_authority_sha256?: unknown; reference?: { contig?: unknown } }) =>
                        typeof row.session_id !== 'string' || !/^[0-9a-f]{24}$/.test(row.session_id)
                        || typeof row.source_authority_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(row.source_authority_sha256)
                        || typeof row.reference?.contig !== 'string')) throw new Error('Native catalog session authority is invalid.');
                setValue(data as Discovery);
            }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(describeNgsError(reason, 'Native read discovery is unavailable.')); });
        return () => controller.abort();
    }, [jobId, revision]);
    if (error) return <section role="alert" className="rounded border p-3 text-xs">{error} <button type="button" className="underline" onClick={() => setRevision((n) => n + 1)}>Refresh discovery</button></section>;
    if (!value) return <p role="status" className="text-xs">Loading read-source discovery…</p>;
    if (!value.native) return fallbackSessionId ? <AlignmentDerivedStatus key={`${jobId}:${fallbackSessionId}`} jobId={jobId} sessionId={fallbackSessionId} signal={signal} onReadAction={onReadAction} onSelectionIntent={onSelectionIntent} selectedReadId={selectedReadId} /> : null;
    return <div className="space-y-2">
        <section aria-label="Original native artifacts" className="rounded border p-3 text-xs">
            <h3 className="font-semibold">Original scientific artifacts</h3>
            <p>Downloads are independent of catalog and preview preparation.</p>
            {artifactError && <p role="alert">{artifactError}</p>}
            <div className="flex flex-wrap gap-2">{artifacts.map((artifact) => <div key={artifact.artifact_id} className="max-w-full rounded border p-1">
                <button type="button" disabled={downloadBusy !== null} className="max-w-full break-all px-2 py-1 disabled:opacity-50"
                    onClick={() => { void download(artifact.artifact_id); }}>{downloadBusy === artifact.artifact_id ? 'Working…' : artifact.filename} · {artifact.size_bytes.toLocaleString()} bytes</button>
                <button type="button" disabled={downloadBusy !== null} className="px-2 py-1 underline disabled:opacity-50"
                    onClick={() => { void download(artifact.artifact_id, true); }}>Retry delivery cache</button>
            </div>)}</div>
        </section>
        {value.unavailable_reason && <p className="rounded border p-3 text-xs">{value.unavailable_reason === 'unsupported_source'
            ? 'This native result has no supported single-reference alignment catalog. Its scientific outputs remain unchanged.'
            : 'Native read-source authority is unavailable. The accepted scientific result was not changed.'}</p>}
        {value.sessions.filter((session) => !fallbackSessionId || session.session_id === fallbackSessionId).map((session) => <section key={session.session_id} aria-label={`Reads aligned to ${session.reference.contig}`}>
            <AlignmentDerivedStatus key={`${jobId}:${session.session_id}`} jobId={jobId} sessionId={session.session_id} signal={signal} onReadAction={onReadAction} onSelectionIntent={onSelectionIntent} selectedReadId={selectedReadId} />
        </section>)}
    </div>;
}
