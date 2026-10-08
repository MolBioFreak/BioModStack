import { useCallback, useEffect, useMemo, useState } from 'react';

import {
    createViewerSnapshot,
    fetchViewerSnapshot,
    fetchViewerSnapshots,
    fetchViewerVolumes,
    type ViewerSnapshotRecordV2,
    type ViewerVolumeInventoryV1,
} from '../../../lib/api';
import {
    canonicalJson,
    createExportManifest,
    createViewerSnapshotV2,
    rowsToCsv,
    sha256Hex,
    type ExportKindV1,
    type ViewerSnapshotBindingV2,
    type ViewerSnapshotV2,
} from '../../contracts/m6Reproducibility';
import type { SpatialVolumeDescriptorV1, VolumePresentationStateV1 } from '../../contracts/spatialVolumes';
import type { StructureSceneController } from '../../runtime/StructureSceneController';
import { supportsGovernedWebMExport, type AuthoritativeFrameStepper } from '../../runtime/browserMovieExport';

const WEBM_VP9_CAPABILITY_PROVEN = import.meta.env.VITE_BMS_WEBM_VP9_CAPABILITY_PROVEN === 'true';

interface M6WorkbenchPanelProps {
    readonly controller: StructureSceneController | null;
    readonly jobId?: string;
    readonly tableRows?: readonly Readonly<Record<string, unknown>>[];
    readonly movieFrameStepper?: AuthoritativeFrameStepper;
}

const messageFor = (result: { status: string; reason?: string; error?: Error }): string => (
    result.status === 'error' ? result.error?.message ?? 'Viewer operation failed' : result.reason ?? `Viewer operation ${result.status}`
);

const download = (blob: Blob, filename: string): void => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
};

const defaultPresentation = (volume: SpatialVolumeDescriptorV1): VolumePresentationStateV1 => ({
    schema: 'bms.viewer.volume-presentation.v1',
    volumeId: volume.volumeId,
    channel: volume.recommendedDisplay?.channel ?? 0,
    visible: true,
    opacity: volume.recommendedDisplay?.opacity ?? 0.45,
    contour: volume.recommendedDisplay?.contourSigma !== undefined && volume.statistics?.mean !== undefined && volume.statistics.sigma !== undefined
        ? { mode: 'sigma', value: volume.recommendedDisplay.contourSigma }
        : { mode: 'absolute', value: volume.recommendedDisplay?.contourAbsolute ?? 1 },
    color: volume.semanticKind === 'electrostatic_potential' ? 0xef4444 : 0x38bdf8,
    representation: 'isosurface',
    slice: null,
    crop: null,
    visibleSegmentIds: [],
    registrationRef: volume.registrationRef ?? null,
});

export function M6WorkbenchPanel({ controller, jobId, tableRows = [], movieFrameStepper }: M6WorkbenchPanelProps) {
    const [inventory, setInventory] = useState<ViewerVolumeInventoryV1 | null>(null);
    const [snapshots, setSnapshots] = useState<readonly ViewerSnapshotRecordV2[]>([]);
    const [presentations, setPresentations] = useState<Readonly<Record<string, VolumePresentationStateV1>>>({});
    const [label, setLabel] = useState('Workbench state');
    const [busy, setBusy] = useState<string | null>(null);
    const [message, setMessage] = useState('M6 resources require exact artifact hashes.');

    const refreshSnapshots = useCallback(async () => {
        if (!jobId) return;
        const response = await fetchViewerSnapshots(jobId);
        setSnapshots(response.data.snapshots);
    }, [jobId]);

    useEffect(() => {
        let active = true;
        setInventory(null);
        if (!jobId) return undefined;
        void Promise.allSettled([fetchViewerVolumes(jobId), fetchViewerSnapshots(jobId)]).then(([volumeResult, snapshotResult]) => {
            if (!active) return;
            if (volumeResult.status === 'fulfilled') {
                const data = volumeResult.value.data;
                setInventory({ ...data, registrations: data.registrations ?? [] });
            }
            if (snapshotResult.status === 'fulfilled') setSnapshots(snapshotResult.value.data.snapshots);
        });
        return () => { active = false; };
    }, [jobId]);

    const execute = async (name: string, operation: () => Promise<void>): Promise<void> => {
        setBusy(name);
        try { await operation(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } finally { setBusy(null); }
    };

    const bindDocuments = useCallback(async (): Promise<readonly ViewerSnapshotBindingV2[]> => {
        if (!controller?.currentScene) throw new Error('Viewer scene is not ready');
        const pairs = await Promise.all(controller.currentScene.documents.map(async (document) => {
            const transport = document.sourceUrl;
            if (!transport) throw new Error(`Document ${document.documentId} has no authorized content transport`);
            const response = await fetch(transport, { credentials: 'same-origin' });
            if (!response.ok) throw new Error(`Document ${document.documentId} could not be verified (${response.status})`);
            return [document.documentId, await sha256Hex(new Uint8Array(await response.arrayBuffer()))] as const;
        }));
        const bound = controller.bindDocumentHashes(Object.fromEntries(pairs));
        if (bound.status !== 'ok') throw new Error(messageFor(bound));
        return pairs.map(([resourceId, sha256]) => ({ kind: 'document', resourceId, sha256, required: true }));
    }, [controller]);

    const prepareSnapshot = useCallback(async (): Promise<{ snapshot: ViewerSnapshotV2; sha256: string }> => {
        if (!controller) throw new Error('Viewer controller is unavailable');
        const documentBindings = await bindDocuments();
        const volumeBindings: ViewerSnapshotBindingV2[] = Object.keys(presentations).map((volumeId) => {
            const volume = inventory?.volumes.find((entry) => entry.volumeId === volumeId);
            if (!volume) throw new Error(`Volume ${volumeId} has no governed descriptor`);
            return { kind: 'volume', resourceId: volumeId, sha256: volume.artifactSha256, required: true };
        });
        const segmentationBindings: ViewerSnapshotBindingV2[] = (inventory?.segmentations ?? [])
            .filter((value) => presentations[value.volumeId])
            .map((value) => ({ kind: 'segmentation', resourceId: value.segmentationId, sha256: value.artifactSha256, required: true }));
        const registrationBindings: ViewerSnapshotBindingV2[] = (inventory?.registrations ?? [])
            .filter((value) => Object.values(presentations).some((state) => state.registrationRef === value.registrationId))
            .map((value) => ({ kind: 'analysis', resourceId: value.registrationId, sha256: value.artifactSha256, required: true }));
        const captured = controller.captureSnapshot();
        if (captured.status !== 'ok') throw new Error(messageFor(captured));
        const snapshot = createViewerSnapshotV2(captured.value.scene, {
            snapshotId: crypto.randomUUID(), capturedAt: new Date().toISOString(), adapterVersion: captured.value.adapterVersion,
            bindings: [...documentBindings, ...volumeBindings, ...segmentationBindings, ...registrationBindings], requiredCapabilities: Object.keys(presentations).length ? ['volume-ccp4-v1'] : [],
            collectionState: null, comparisonState: null, volumeStates: Object.values(presentations), uiComposition: 'standard',
            provenance: captured.value.scene.provenance,
        });
        return { snapshot, sha256: await sha256Hex(canonicalJson(snapshot)) };
    }, [bindDocuments, controller, inventory?.volumes, inventory?.segmentations, inventory?.registrations, presentations]);

    const saveSnapshot = () => void execute('snapshot:save', async () => {
        if (!jobId) throw new Error('A job-owned scene is required for immutable snapshot persistence');
        const prepared = await prepareSnapshot();
        await createViewerSnapshot(jobId, label, prepared.snapshot, prepared.sha256);
        await refreshSnapshots();
        setMessage(`Saved immutable snapshot ${prepared.snapshot.snapshotId}`);
    });

    const restoreSnapshot = (record: ViewerSnapshotRecordV2) => void execute(`snapshot:${record.snapshotId}`, async () => {
        if (!controller || !jobId) throw new Error('Viewer controller and job context are required');
        const response = await fetchViewerSnapshot(jobId, record.snapshotId);
        const snapshot = response.data.snapshot;
        if (!snapshot) throw new Error('Snapshot payload is absent');
        const documentBindings = await bindDocuments();
        const volumeBindings: ViewerSnapshotBindingV2[] = (inventory?.volumes ?? []).map((volume) => ({
            kind: 'volume', resourceId: volume.volumeId, sha256: volume.artifactSha256, required: true,
        }));
        const segmentationBindings: ViewerSnapshotBindingV2[] = (inventory?.segmentations ?? []).map((value) => ({
            kind: 'segmentation', resourceId: value.segmentationId, sha256: value.artifactSha256, required: true,
        }));
        const registrationBindings: ViewerSnapshotBindingV2[] = (inventory?.registrations ?? []).map((value) => ({
            kind: 'analysis', resourceId: value.registrationId, sha256: value.artifactSha256, required: true,
        }));
        const restored = await controller.restoreSnapshotV2(snapshot, [...documentBindings, ...volumeBindings, ...segmentationBindings, ...registrationBindings], {
            volumes: inventory?.volumes ?? [], segmentations: inventory?.segmentations ?? [], registrations: inventory?.registrations ?? [],
        });
        if (restored.status !== 'ok') throw new Error(messageFor(restored));
        setPresentations(Object.fromEntries(snapshot.volumeStates.map((state) => [state.volumeId, state])));
        setMessage(`Restored snapshot ${record.snapshotId}`);
    });

    const exportArtifact = (kind: ExportKindV1) => void execute(`export:${kind}`, async () => {
        if (!controller || !jobId) throw new Error('A ready job-owned viewer is required for governed export');
        const prepared = await prepareSnapshot();
        let output: Blob;
        let filename: string;
        if (kind === 'snapshot_json') {
            output = new Blob([canonicalJson(prepared.snapshot)], { type: 'application/json' }); filename = `bms-${prepared.snapshot.snapshotId}.snapshot.json`;
        } else if (kind === 'figure_png') {
            const result = await controller.capturePng(); if (result.status !== 'ok') throw new Error(messageFor(result));
            output = result.value; filename = `bms-${prepared.snapshot.snapshotId}.png`;
        } else if (kind === 'selection_mmcif') {
            const result = await controller.exportSelectionMmcif(); if (result.status !== 'ok') throw new Error(messageFor(result));
            output = result.value; filename = `bms-${prepared.snapshot.snapshotId}.selection.cif`;
        } else if (kind === 'table_csv') {
            const columns = [...new Set(tableRows.flatMap((row) => Object.keys(row)))].sort();
            output = new Blob([rowsToCsv(tableRows, columns)], { type: 'text/csv' }); filename = `bms-${prepared.snapshot.snapshotId}.table.csv`;
        } else {
            output = new Blob([canonicalJson(tableRows)], { type: 'application/json' }); filename = `bms-${prepared.snapshot.snapshotId}.table.json`;
        }
        const bytes = new Uint8Array(await output.arrayBuffer());
        const manifest = await createExportManifest({
            exportId: crypto.randomUUID(), kind, createdAt: new Date().toISOString(), jobId,
            workflowContext: { viewer: 'structure-workbench' }, snapshot: prepared.snapshot,
            exportParameters: { rowCount: tableRows.length }, outputFileName: filename, output: bytes,
        });
        download(output, filename);
        download(new Blob([canonicalJson(manifest)], { type: 'application/json' }), `${filename}.manifest.json`);
        setMessage(`Exported ${kind}; SHA-256 ${manifest.outputSha256}`);
    });

    const exportMovie = () => void execute('export:webm', async () => {
        if (!controller || !jobId || !movieFrameStepper) throw new Error('An authoritative trajectory or morph frame source is required');
        const prepared = await prepareSnapshot();
        const kind: ExportKindV1 = movieFrameStepper.sourceKind === 'coordinate_trajectory' ? 'trajectory_webm' : 'morph_webm';
        const filename = `bms-${prepared.snapshot.snapshotId}.${movieFrameStepper.sourceKind === 'coordinate_trajectory' ? 'trajectory' : 'morph'}.webm`;
        const request = {
            fps: 30, bitrate: 8_000_000, outputFileName: filename, codec: 'video/webm;codecs=vp9' as const,
            sourceSnapshotSha256: prepared.sha256, capabilityProven: WEBM_VP9_CAPABILITY_PROVEN,
        };
        const result = await controller.exportWebM(movieFrameStepper, request, (frames) => setMessage(`Encoding frame ${frames}/${movieFrameStepper.frames.length}`));
        if (result.status !== 'ok') throw new Error(messageFor(result));
        const bytes = new Uint8Array(await result.value.blob.arrayBuffer());
        const baseManifest = await createExportManifest({
            exportId: crypto.randomUUID(), kind, createdAt: new Date().toISOString(), jobId,
            workflowContext: { viewer: 'structure-workbench', sourceKind: movieFrameStepper.sourceKind }, snapshot: prepared.snapshot,
            exportParameters: { fps: result.value.fps, bitrate: result.value.bitrate, codec: result.value.codec }, outputFileName: filename, output: bytes,
        });
        const manifest = {
            ...baseManifest,
            movie: {
                sourceKind: movieFrameStepper.sourceKind, provenanceRef: movieFrameStepper.provenanceRef,
                sourceBindings: movieFrameStepper.sourceBindings, frames: movieFrameStepper.frames,
                completedFrames: result.value.completedFrames, sourceFrameRange: result.value.sourceFrameRange,
                width: result.value.width, height: result.value.height, fps: result.value.fps,
                bitrate: result.value.bitrate, codec: result.value.codec, semanticWarnings: result.value.semanticWarnings,
            },
        };
        download(result.value.blob, filename);
        download(new Blob([canonicalJson(manifest)], { type: 'application/json' }), `${filename}.manifest.json`);
        setMessage(`Exported ${kind}; SHA-256 ${baseManifest.outputSha256}`);
    });

    const loadVolume = (volume: SpatialVolumeDescriptorV1, presentation = defaultPresentation(volume)) => void execute(`volume:${volume.volumeId}`, async () => {
        if (!controller) throw new Error('Viewer controller is unavailable');
        const segmentation = inventory?.segmentations.find((entry) => entry.volumeId === volume.volumeId);
        if (volume.semanticKind === 'segmentation' && !segmentation) throw new Error('Supplied segmentation metadata is unavailable');
        const finalPresentation: VolumePresentationStateV1 = segmentation
            ? { ...presentation, visibleSegmentIds: segmentation.labels.map((entry) => entry.segmentId), representation: 'isosurface', slice: null }
            : presentation;
        const initialPresentation: VolumePresentationStateV1 = segmentation
            ? { ...finalPresentation, visible: false, visibleSegmentIds: [] }
            : finalPresentation;
        const loaded = await controller.loadVolume(volume, initialPresentation);
        if (loaded.status !== 'ok') throw new Error(messageFor(loaded));
        const registration = inventory?.registrations.find((entry) => entry.registrationId === volume.registrationRef);
        if (volume.registrationRef && !registration) throw new Error(`Required supplied registration ${volume.registrationRef} is unavailable`);
        if (registration) {
            const applied = await controller.applyVolumeRegistration(registration);
            if (applied.status !== 'ok') throw new Error(messageFor(applied));
        }
        if (segmentation) {
            const applied = await controller.applyVolumeSegmentation(segmentation);
            if (applied.status !== 'ok') throw new Error(messageFor(applied));
        }
        if (registration || segmentation) {
            const represented = await controller.setVolumePresentation(finalPresentation);
            if (represented.status !== 'ok') throw new Error(messageFor(represented));
        }
        setPresentations((current) => ({ ...current, [volume.volumeId]: finalPresentation }));
        setMessage(`Loaded verified ${volume.semanticKind} volume ${volume.volumeId}`);
    });

    const setRepresentation = (volume: SpatialVolumeDescriptorV1, representation: 'isosurface' | 'slice') => {
        const current = presentations[volume.volumeId] ?? defaultPresentation(volume);
        const next: VolumePresentationStateV1 = {
            ...current, representation,
            slice: representation === 'slice' ? { axis: 2, index: Math.floor(volume.dimensions[2] / 2) } : null,
        };
        void execute(`volume:presentation:${volume.volumeId}`, async () => {
            if (!controller) throw new Error('Viewer controller is unavailable');
            const result = presentations[volume.volumeId] ? await controller.setVolumePresentation(next) : await controller.loadVolume(volume, next);
            if (result.status !== 'ok') throw new Error(messageFor(result));
            setPresentations((state) => ({ ...state, [volume.volumeId]: next }));
        });
    };

    const volumeRows = useMemo(() => inventory?.volumes ?? [], [inventory]);

    return (
        <section className="space-y-2 rounded border border-slate-700 bg-slate-950/90 p-2 text-xs text-slate-200" aria-label="M6 reproducibility and spatial resources">
            <div className="font-semibold">Reproducibility, volumes, and exports</div>
            <div className="flex gap-1"><input value={label} onChange={(event) => setLabel(event.target.value)} maxLength={120} className="min-w-0 flex-1 rounded bg-slate-800 px-2 py-1" /><button onClick={saveSnapshot} disabled={!controller || !jobId || Boolean(busy)} className="rounded bg-blue-700 px-2 py-1 disabled:opacity-40">Save snapshot</button></div>
            {snapshots.length > 0 && <div className="max-h-24 space-y-1 overflow-auto">{snapshots.map((snapshot) => <button key={snapshot.snapshotId} onClick={() => restoreSnapshot(snapshot)} disabled={Boolean(busy)} className="block w-full rounded bg-slate-800 px-2 py-1 text-left disabled:opacity-40">Restore {snapshot.label} · {snapshot.snapshotSha256.slice(0, 10)}</button>)}</div>}
            <div className="flex flex-wrap gap-1">
                <button onClick={() => exportArtifact('figure_png')} disabled={!controller || !jobId || Boolean(busy)} className="rounded bg-emerald-700 px-2 py-1">PNG</button>
                <button onClick={() => exportArtifact('table_csv')} disabled={!controller || !jobId || !tableRows.length || Boolean(busy)} className="rounded bg-emerald-700 px-2 py-1">CSV</button>
                <button onClick={() => exportArtifact('table_json')} disabled={!controller || !jobId || !tableRows.length || Boolean(busy)} className="rounded bg-emerald-700 px-2 py-1">JSON</button>
                <button onClick={() => exportArtifact('selection_mmcif')} disabled={!controller || !jobId || Boolean(busy)} className="rounded bg-emerald-700 px-2 py-1">Selected mmCIF</button>
                <button onClick={() => exportArtifact('snapshot_json')} disabled={!controller || !jobId || Boolean(busy)} className="rounded bg-emerald-700 px-2 py-1">Snapshot JSON</button>
            </div>
            {volumeRows.length > 0 ? <div className="space-y-1">{volumeRows.map((volume) => <div key={volume.volumeId} className="rounded border border-slate-800 px-2 py-1 text-[11px]"><div>{volume.semanticKind} · {volume.dimensions.join('×')} · {volume.valueUnits ?? 'units unspecified'} · {volume.artifactSha256.slice(0, 10)}</div>{volume.semanticKind !== 'segmentation' ? <div className="mt-1 flex gap-1"><button onClick={() => loadVolume(volume)} disabled={Boolean(busy)} className="rounded bg-cyan-800 px-2 py-0.5">Load</button><button onClick={() => setRepresentation(volume, 'isosurface')} disabled={Boolean(busy)} className="rounded bg-slate-700 px-2 py-0.5">Isosurface</button><button onClick={() => setRepresentation(volume, 'slice')} disabled={Boolean(busy)} className="rounded bg-slate-700 px-2 py-0.5">Z slice</button><button onClick={() => void execute(`remove:${volume.volumeId}`, async () => { const result = await controller!.removeVolume(volume.volumeId); if (result.status !== 'ok') throw new Error(messageFor(result)); setPresentations((state) => Object.fromEntries(Object.entries(state).filter(([id]) => id !== volume.volumeId))); })} disabled={!presentations[volume.volumeId] || Boolean(busy)} className="rounded bg-red-900 px-2 py-0.5">Remove</button></div> : <div className="mt-1 flex items-center gap-1"><button onClick={() => loadVolume(volume)} disabled={Boolean(busy)} className="rounded bg-cyan-800 px-2 py-0.5">Load supplied segments</button><button onClick={() => void execute(`remove:${volume.volumeId}`, async () => { const result = await controller!.removeVolume(volume.volumeId); if (result.status !== 'ok') throw new Error(messageFor(result)); setPresentations((state) => Object.fromEntries(Object.entries(state).filter(([id]) => id !== volume.volumeId))); })} disabled={!presentations[volume.volumeId] || Boolean(busy)} className="rounded bg-red-900 px-2 py-0.5">Remove</button><span className="text-amber-300">Exact integer voxel labels only; no browser-derived segmentation.</span></div>}</div>)}</div> : <div className="text-[11px] text-slate-500">No supplied CCP4/MRC volume manifest for this job.</div>}
            {(inventory?.segmentations.length ?? 0) > 0 && <details><summary>Supplied segment labels</summary>{inventory!.segmentations.map((segmentation) => <div key={segmentation.segmentationId} className="mt-1 rounded border border-slate-800 p-1"><div>{segmentation.segmentationId} · {segmentation.artifactSha256.slice(0, 10)}</div>{segmentation.labels.map((entry) => <div key={entry.segmentId} className="pl-2">#{entry.segmentId} {entry.label ?? 'unknown'}{entry.parentSegmentId === null ? '' : ` · parent ${entry.parentSegmentId}`}</div>)}</div>)}</details>}
            <div className="flex items-center gap-1 rounded border border-slate-800 px-2 py-1 text-[11px] text-slate-400"><span>WebM/VP9: {supportsGovernedWebMExport() ? WEBM_VP9_CAPABILITY_PROVEN ? movieFrameStepper ? 'ready with authoritative source' : 'proven, no authoritative source attached' : 'browser substrate present; ffprobe capability proof is not enabled' : 'unsupported by this browser'}.</span><button onClick={exportMovie} disabled={!movieFrameStepper || !WEBM_VP9_CAPABILITY_PROVEN || Boolean(busy)} className="rounded bg-violet-800 px-2 py-0.5 disabled:opacity-40">Export WebM</button>{busy === 'export:webm' && <button onClick={() => controller?.cancelCurrentExport()} className="rounded bg-red-900 px-2 py-0.5">Cancel</button>}</div>
            <div role="status" className="text-[11px] text-slate-400">{busy ? `${busy}…` : message}</div>
        </section>
    );
}
