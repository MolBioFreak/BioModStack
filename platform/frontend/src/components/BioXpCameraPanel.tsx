import { useCallback, useEffect, useRef, useState } from 'react';

import {
    bioXpErrorText,
    buildBioXpCameraMjpegUrl,
    captureBioXpCameraSnapshot,
    fetchBioXpCameraFrame,
    getBioXpCameraIllumination,
    setBioXpCameraIllumination,
    setBioXpCameraRgb,
    type BioXpCameraIllumination,
    type BioXpIlluminationChannel,
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

const RGB_PRESETS = [
    { label: 'White', rgb: [255, 255, 255] },
    { label: 'Red', rgb: [255, 0, 0] },
    { label: 'Green', rgb: [0, 255, 0] },
    { label: 'Blue', rgb: [0, 0, 255] },
    { label: 'Off', rgb: [0, 0, 0] },
] as const;

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
    const [streamOverride, setStreamOverride] = useState<{ active: boolean; baseline: typeof streamQuery.data; stream?: typeof streamQuery.data } | null>(null);
    const [pendingAction, setPendingAction] = useState<'latest' | 'snapshot' | 'stream' | null>(null);
    const [presentationNowMs, setPresentationNowMs] = useState(() => performance.now());
    const [, bumpPresentationRevision] = useState(0);
    const illuminationReadRef = useRef(0);
    const illuminationPendingRef = useRef(new Set<BioXpIlluminationChannel>());
    const [illuminationPending, setIlluminationPending] = useState(new Set<BioXpIlluminationChannel>());
    const [illumination, setIllumination] = useState<{ session: typeof sessionRef.current; data: BioXpCameraIllumination } | null>(null);
    const [illuminationError, setIlluminationError] = useState<string | null>(null);
    const rgbPendingRef = useRef(false);
    const [rgbPending, setRgbPending] = useState(false);
    const [rgbLastCommand, setRgbLastCommand] = useState<{ session: typeof sessionRef.current; label: string } | null>(null);
    const [rgbError, setRgbError] = useState<string | null>(null);

    const commandRgb = useCallback(async (preset: typeof RGB_PRESETS[number]) => {
        const session = sessionRef.current;
        if (!session.connected || session.generation === null || !mutationEnabled || rgbPendingRef.current) return;
        const isCurrent = () => mountedRef.current && sessionRef.current === session;
        rgbPendingRef.current = true;
        setRgbPending(true);
        setRgbError(null);
        try {
            const result = await setBioXpCameraRgb(session.generation, preset.rgb);
            if (!isCurrent()) return;
            if (result.connection_generation !== session.generation) throw new Error('RGB response belongs to a previous connection');
            if (!result.ok) throw new Error('RGB light command failed: robot did not acknowledge all channels');
            setRgbLastCommand({ session, label: preset.label });
        } catch (error) {
            if (isCurrent()) setRgbError(bioXpErrorText(error));
        } finally {
            if (isCurrent()) {
                rgbPendingRef.current = false;
                setRgbPending(false);
            }
        }
    }, [mutationEnabled]);

    const refreshIllumination = useCallback(async () => {
        const session = sessionRef.current;
        const token = ++illuminationReadRef.current;
        setIllumination(null);
        if (!session.connected || session.generation === null) return;
        const isCurrent = () => mountedRef.current && sessionRef.current === session && token === illuminationReadRef.current;
        try {
            const data = await getBioXpCameraIllumination(session.generation);
            if (!isCurrent()) return;
            if (data.connection_generation !== session.generation) throw new Error('Illumination belongs to a previous connection');
            setIllumination({ session, data });
        } catch (error) {
            if (isCurrent()) setIlluminationError(bioXpErrorText(error));
        }
    }, []);

    const commandIllumination = useCallback(async (channel: BioXpIlluminationChannel, on: boolean) => {
        const session = sessionRef.current;
        if (!session.connected || session.generation === null || !mutationEnabled || illuminationPendingRef.current.has(channel)) return;
        const isCurrent = () => mountedRef.current && sessionRef.current === session;
        illuminationPendingRef.current.add(channel);
        setIlluminationPending(new Set(illuminationPendingRef.current));
        ++illuminationReadRef.current;
        setIllumination(null);
        setIlluminationError(null);
        try {
            const data = await setBioXpCameraIllumination(session.generation, channel, on);
            if (!isCurrent()) return;
            if (data.connection_generation !== session.generation) throw new Error('Illumination belongs to a previous connection');
        } catch (error) {
            if (isCurrent()) setIlluminationError(bioXpErrorText(error));
        } finally {
            if (isCurrent()) {
                illuminationPendingRef.current.delete(channel);
                setIlluminationPending(new Set(illuminationPendingRef.current));
                void refreshIllumination();
            }
        }
    }, [mutationEnabled, refreshIllumination]);

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
        illuminationPendingRef.current = new Set();
        setIlluminationPending(new Set());
        setIlluminationError(null);
        rgbPendingRef.current = false;
        setRgbPending(false);
        setRgbLastCommand(null);
        setRgbError(null);
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
    // The robot camera can be replaced without replacing the BMS connection
    // (including another tab's stop/start entirely between our polls). Reattach
    // the ended MJPEG reader only for a new camera owner, never for poll churn.
    const effectiveStream = streamOverride && streamOverride.baseline === streamQuery.data && streamOverride.stream
        ? streamOverride.stream : streamQuery.data;
    const mediaOwner = effectiveStreamActive
        ? `${connectionGeneration}:${effectiveStream?.camera_ownership_epoch}:${effectiveStream?.stream_id}`
        : imageUrl;

    // Read cached evidence on actual preview ownership transitions, not poll churn.
    const illuminationOwner = effectiveStream?.connection_generation === connectionGeneration
        ? `${effectiveStreamActive}:${effectiveStream?.camera_ownership_epoch}:${effectiveStream?.stream_id}` : null;
    useEffect(() => {
        void refreshIllumination();
    }, [connected, connectionGeneration, illuminationOwner, refreshIllumination]);

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
                setStreamOverride({ active: result.active, baseline: streamQuery.data, stream: result });
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
        ? buildBioXpCameraMjpegUrl(connectionGeneration, effectiveStream?.stream_id)
        : imageSessionRef.current === sessionRef.current ? imageUrl : null;

    return (
        <section aria-label="Camera" className="rounded-xl border border-sky-800/70 bg-sky-950/20 p-3">
            <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">Camera</h2>
                <span className="text-sm text-slate-400">{cameraState}</span>
            </div>

            <div className="mt-2 flex aspect-[4/3] w-full items-center justify-center overflow-hidden rounded-lg border border-slate-800 bg-black">
                {mediaUrl
                    ? <img key={mediaOwner} src={mediaUrl} alt={effectiveStreamActive ? 'BioXP live camera' : 'BioXP camera'} className="h-full w-full object-contain" onLoad={() => setImageError(null)} onError={() => setImageError('Camera stream or frame could not be displayed')} />
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
            <div role="group" aria-label="Camera illumination" className="mt-3 border-t border-slate-800 pt-2">
                <p className="text-xs text-slate-400">Camera lights · Last command (not optical readback)</p>
                <div className="mt-2 flex flex-wrap gap-3">
                    {([1, 2, 3] as const).map((channel) => {
                        const on = illumination?.session === sessionRef.current
                            ? illumination.data.channels.find((row) => row.channel === channel)?.on : null;
                        return <div key={channel} role="group" aria-label={`LED${channel}`} className="flex min-w-0 flex-wrap items-center gap-2">
                            <span className="text-sm">LED{channel}: {on == null ? 'Unknown' : on ? 'On' : 'Off'}</span>
                            {([true, false] as const).map((value) => <button
                                key={String(value)} type="button" aria-label={`LED${channel} ${value ? 'On' : 'Off'}`}
                                disabled={!connected || !mutationEnabled || connectionGeneration === null || illuminationPending.has(channel)}
                                onClick={() => void commandIllumination(channel, value)}
                                className="rounded bg-sky-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                            >{value ? 'On' : 'Off'}</button>)}
                        </div>;
                    })}
                </div>
                {illuminationError && <p role="alert" className="mt-2 text-sm text-red-300">{illuminationError}</p>}
            </div>
            <div role="group" aria-label="RGB deck/ring light" className="mt-3 border-t border-slate-800 pt-2">
                <p className="text-xs text-slate-400">RGB deck/ring light · Last successful command: {rgbLastCommand?.session === sessionRef.current ? rgbLastCommand.label : 'Unknown'} (not optical readback)</p>
                <div className="mt-2 flex flex-wrap gap-2">
                    {RGB_PRESETS.map((preset) => <button key={preset.label} type="button" aria-label={`RGB ${preset.label}`}
                        disabled={!connected || !mutationEnabled || connectionGeneration === null || rgbPending}
                        onClick={() => void commandRgb(preset)}
                        className="rounded bg-sky-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                    >{preset.label}</button>)}
                </div>
                {rgbError && <p role="alert" className="mt-2 text-sm text-red-300">{rgbError}</p>}
            </div>
            {streamQuery.data?.state === 'error' && streamQuery.data.last_error && <p role="alert" className="mt-2 text-sm text-red-300">{streamQuery.data.last_error}</p>}
            {statusQuery.isError && pendingAction !== 'snapshot' && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(statusQuery.error)}</p>}
            {streamQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(streamQuery.error)}</p>}
            {imageError && <p role="alert" className="mt-2 text-sm text-red-300">{imageError}</p>}
        </section>
    );
}
