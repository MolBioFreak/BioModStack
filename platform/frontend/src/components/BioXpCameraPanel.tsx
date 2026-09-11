import { useCallback, useEffect, useRef, useState } from 'react';

import {
    bioXpErrorText,
    buildBioXpCameraMjpegUrl,
    captureBioXpCameraSnapshot,
    fetchBioXpCameraFrame,
    startBioXpCameraStream,
    stopBioXpCameraStream,
    useBioXpCameraStatus,
    useBioXpCameraStreamState,
} from '../lib/bioxpClient';
import {
    type CameraObjectUrlOwner,
    createCameraObjectUrlOwner,
    deriveBioXpCameraPresentation,
} from './bioxpCameraState';

interface BioXpCameraPanelProps {
    connected: boolean;
    connectionGeneration: number | null;
    mutationEnabled: boolean;
}

export function BioXpCameraPanel({
    connected,
    connectionGeneration,
    mutationEnabled,
}: BioXpCameraPanelProps) {
    const statusQuery = useBioXpCameraStatus(connectionGeneration, connected);
    const streamQuery = useBioXpCameraStreamState(connectionGeneration, connected);
    const refetchStatus = statusQuery.refetch;
    const refetchStream = streamQuery.refetch;
    const ownerRef = useRef<CameraObjectUrlOwner | null>(null);
    const mountedRef = useRef(false);
    const sessionRef = useRef({ connected, generation: connectionGeneration });
    if (sessionRef.current.connected !== connected || sessionRef.current.generation !== connectionGeneration) {
        sessionRef.current = { connected, generation: connectionGeneration };
    }
    const streamRequestRef = useRef(0);
    const requestPendingRef = useRef(false);
    const providerGenerationRef = useRef<number | null>(null);
    const sequenceAdvancingRef = useRef(false);
    const statusReceivedAtRef = useRef(performance.now());
    const frameAgeFloorMsRef = useRef(0);
    const lastSequenceRef = useRef<number | null>(null);
    const lastSequenceAdvanceAtRef = useRef(performance.now());
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    const [imageError, setImageError] = useState<string | null>(null);
    const imageSessionRef = useRef(sessionRef.current);
    const [streamOverride, setStreamOverride] = useState<{ active: boolean; baseline: typeof streamQuery.data } | null>(null);
    const [pendingAction, setPendingAction] = useState<'latest' | 'snapshot' | 'stream' | null>(null);
    const [presentationNowMs, setPresentationNowMs] = useState(() => performance.now());
    const [, bumpPresentationRevision] = useState(0);

    useEffect(() => {
        mountedRef.current = true;
        const owner = createCameraObjectUrlOwner();
        ownerRef.current = owner;
        return () => {
            mountedRef.current = false;
            owner.dispose();
            if (ownerRef.current === owner) ownerRef.current = null;
        };
    }, []);

    useEffect(() => {
        ownerRef.current?.clear();
        setImageUrl(null);
        setImageError(null);
        setStreamOverride(null);
        streamRequestRef.current += 1;
        requestPendingRef.current = false;
        setPendingAction(null);
        const now = performance.now();
        statusReceivedAtRef.current = now;
        lastSequenceRef.current = null;
        providerGenerationRef.current = null;
        sequenceAdvancingRef.current = false;
        lastSequenceAdvanceAtRef.current = now;
        setPresentationNowMs(now);
        bumpPresentationRevision((revision) => revision + 1);
    }, [connectionGeneration, connected]);

    useEffect(() => {
        if (!connected || !statusQuery.data || statusQuery.isError
            || statusQuery.data.requestConnectionGeneration !== connectionGeneration) return;
        const receivedAt = statusQuery.data.receivedAtMonotonicMs ?? performance.now();
        const sequence = statusQuery.data.frame_sequence;
        const upstreamAndTransitMs = statusQuery.data.state === 'stale' ? Infinity
            : (statusQuery.data.frame_age_seconds ?? Infinity) * 1_000
                + (statusQuery.data.requestElapsedMs ?? 0);
        // A repeated source identity cannot become younger just because a newer
        // response reports a smaller upstream age or a shorter browser transit.
        const sameFrame = providerGenerationRef.current === statusQuery.data.provider_generation
            && lastSequenceRef.current === sequence;
        frameAgeFloorMsRef.current = sameFrame
            ? Math.max(upstreamAndTransitMs, frameAgeFloorMsRef.current + Math.max(0, receivedAt - statusReceivedAtRef.current))
            : upstreamAndTransitMs;
        statusReceivedAtRef.current = receivedAt;
        if (providerGenerationRef.current !== statusQuery.data.provider_generation) {
            providerGenerationRef.current = statusQuery.data.provider_generation;
            lastSequenceRef.current = null;
            sequenceAdvancingRef.current = false;
        }
        if (sequence !== null && (lastSequenceRef.current === null || sequence > lastSequenceRef.current)) {
            sequenceAdvancingRef.current = lastSequenceRef.current !== null;
            lastSequenceRef.current = sequence;
            lastSequenceAdvanceAtRef.current = receivedAt;
        } else if (sequence === null || sequence < (lastSequenceRef.current ?? 0)) {
            sequenceAdvancingRef.current = false;
        }
        setPresentationNowMs(performance.now());
        bumpPresentationRevision((revision) => revision + 1);
    }, [statusQuery.data, statusQuery.dataUpdatedAt, statusQuery.isError, connected, connectionGeneration]);

    const loadImage = useCallback(async (source: 'latest' | 'snapshot') => {
        const owner = ownerRef.current;
        if (!owner || connectionGeneration === null || !connected || requestPendingRef.current) return;
        const session = sessionRef.current;
        const token = owner.begin();
        const isCurrent = () => sessionRef.current === session && owner.isCurrent(token) && mountedRef.current;
        requestPendingRef.current = true;
        setPendingAction(source);
        setImageError(null);
        try {
            const image = source === 'snapshot'
                ? await captureBioXpCameraSnapshot(connectionGeneration)
                : await fetchBioXpCameraFrame(connectionGeneration);
            if (image.connectionGeneration !== connectionGeneration) {
                throw new Error('Camera frame belongs to a previous connection');
            }
            if (!isCurrent()) return;
            const nextUrl = owner.adopt(token, image.blob);
            if (nextUrl && isCurrent()) {
                imageSessionRef.current = session;
                setImageUrl(nextUrl);
            }
        } catch (error) {
            if (isCurrent()) setImageError(bioXpErrorText(error));
        } finally {
            if (isCurrent()) {
                await refetchStatus().catch(() => undefined);
                if (isCurrent()) {
                    requestPendingRef.current = false;
                    setPendingAction(null);
                }
            }
        }
    }, [connected, connectionGeneration, refetchStatus]);

    const serverStreamActive = streamQuery.data?.connection_generation === connectionGeneration && streamQuery.data?.active === true;
    // A completed local Stop must not be undone by a late pre-Stop poll.
    // Only another explicit start or a new connection releases that local intent.
    const effectiveStreamActive = connected && connectionGeneration !== null && (streamOverride?.active === false
        ? false : streamOverride && streamOverride.baseline === streamQuery.data ? streamOverride.active : serverStreamActive);

    const toggleStream = useCallback(async () => {
        if (connectionGeneration === null || !connected || !mutationEnabled || requestPendingRef.current) return;
        const session = sessionRef.current;
        const token = ++streamRequestRef.current;
        const isCurrent = () => mountedRef.current && sessionRef.current === session && streamRequestRef.current === token;
        requestPendingRef.current = true;
        setPendingAction('stream');
        setImageError(null);
        try {
            const result = effectiveStreamActive
                ? await stopBioXpCameraStream(connectionGeneration)
                : await startBioXpCameraStream(connectionGeneration);
            if (!isCurrent()) return;
            if (result.connection_generation !== connectionGeneration) {
                throw new Error('Camera stream belongs to a previous connection');
            }
            if (effectiveStreamActive) {
                setStreamOverride({ active: false, baseline: streamQuery.data });
            } else {
                setStreamOverride({ active: result.active, baseline: streamQuery.data });
            }
            await refetchStream().catch(() => undefined);
        } catch (error) {
            if (isCurrent()) setImageError(bioXpErrorText(error));
        } finally {
            if (isCurrent()) {
                requestPendingRef.current = false;
                setPendingAction(null);
            }
        }
    }, [connected, connectionGeneration, effectiveStreamActive, mutationEnabled, refetchStream, streamQuery.data]);

    const cameraStatus = statusQuery.isError || statusQuery.data?.requestConnectionGeneration !== connectionGeneration
        ? undefined : statusQuery.data;
    useEffect(() => {
        if (!connected
            || !cameraStatus
            || !cameraStatus.available
            || cameraStatus.state !== 'live'
            || cameraStatus.frame_age_seconds === null) return;
        const budgetMs = cameraStatus.freshness_budget_seconds * 1_000;
        const effectiveAgeMs = Math.max(frameAgeFloorMsRef.current,
            cameraStatus.frame_age_seconds * 1_000 + (cameraStatus.requestElapsedMs ?? 0))
            + Math.max(0, presentationNowMs - statusReceivedAtRef.current);
        const frameRemainingMs = budgetMs - effectiveAgeMs;
        const sequenceRemainingMs = budgetMs
            - Math.max(0, presentationNowMs - lastSequenceAdvanceAtRef.current);
        const expiryDelayMs = Math.min(frameRemainingMs, sequenceRemainingMs);
        if (expiryDelayMs <= 0) return;
        const timer = window.setTimeout(
            () => {
                setPresentationNowMs(performance.now());
                bumpPresentationRevision((revision) => revision + 1);
            },
            Math.ceil(expiryDelayMs),
        );
        return () => window.clearTimeout(timer);
    }, [cameraStatus, connected, presentationNowMs]);

    const presentation = deriveBioXpCameraPresentation({
        // Presentation-only age floor; query data retains original upstream fields.
        status: cameraStatus ? { ...cameraStatus,
            frame_age_seconds: Math.max(frameAgeFloorMsRef.current / 1_000,
                (cameraStatus.frame_age_seconds ?? Infinity) + (cameraStatus.requestElapsedMs ?? 0) / 1_000),
            requestElapsedMs: 0,
        } : null,
        statusReceivedAtMs: statusReceivedAtRef.current,
        lastSequenceAdvanceAtMs: lastSequenceAdvanceAtRef.current,
        nowMs: presentationNowMs,
        error: statusQuery.isError ? bioXpErrorText(statusQuery.error) : null,
    });
    const cameraState = !connected
        ? 'Disconnected'
        : imageError || streamQuery.isError || streamQuery.data?.state === 'error' || presentation.label === 'UNAVAILABLE'
            ? 'Unavailable'
            : presentation.label === 'STALE' ? 'Stale'
                : effectiveStreamActive
                    ? streamQuery.data?.state === 'live' && streamQuery.data?.connection_generation === connectionGeneration && sequenceAdvancingRef.current
                        ? 'Video live' : 'Waiting for advancing frames'
                    : 'Ready';
    const mediaUrl = !connected ? null : effectiveStreamActive && connectionGeneration !== null
        ? buildBioXpCameraMjpegUrl(connectionGeneration)
        : imageSessionRef.current === sessionRef.current ? imageUrl : null;

    return (
        <section className="rounded-xl border border-sky-800/70 bg-sky-950/20 p-3">
            <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">Camera</h2>
                <span className="text-sm text-slate-400">{cameraState}</span>
            </div>

            <div className="mt-2 flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-lg border border-slate-800 bg-black">
                {mediaUrl
                    ? <img src={mediaUrl} alt={effectiveStreamActive ? 'BioXP live camera' : 'BioXP camera'} className="h-full w-full object-contain" onError={() => setImageError('Camera stream or frame could not be displayed')} />
                    : <span className="text-sm text-slate-500">No frame loaded</span>}
            </div>

            <div className="mt-2 flex flex-wrap gap-2">
                <button
                    type="button"
                    disabled={!connected || !mutationEnabled || connectionGeneration === null || pendingAction !== null}
                    onClick={() => void toggleStream()}
                    className="rounded bg-emerald-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                >{pendingAction === 'stream' ? 'Working…' : effectiveStreamActive ? 'Stop Video' : 'Video'}</button>
                <button
                    type="button"
                    disabled={!connected || connectionGeneration === null || pendingAction !== null || effectiveStreamActive}
                    onClick={() => void loadImage('latest')}
                    className="rounded bg-sky-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                >{pendingAction === 'latest' ? 'Loading…' : 'Refresh'}</button>
                <button
                    type="button"
                    disabled={!connected || !mutationEnabled || connectionGeneration === null || pendingAction !== null || effectiveStreamActive}
                    onClick={() => void loadImage('snapshot')}
                    className="rounded bg-indigo-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                >{pendingAction === 'snapshot' ? 'Capturing…' : 'Capture'}</button>
            </div>

            {effectiveStreamActive && presentation.label === 'STALE' && <p className="mt-2 text-xs text-amber-300">Video frames are stale.</p>}
            {streamQuery.data?.state === 'error' && streamQuery.data.last_error && <p role="alert" className="mt-2 text-sm text-red-300">{streamQuery.data.last_error}</p>}
            {statusQuery.isError && pendingAction !== 'snapshot' && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(statusQuery.error)}</p>}
            {streamQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(streamQuery.error)}</p>}
            {imageError && <p role="alert" className="mt-2 text-sm text-red-300">{imageError}</p>}
        </section>
    );
}
