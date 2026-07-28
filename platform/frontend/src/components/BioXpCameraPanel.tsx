import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
    bioXpErrorText,
    captureBioXpCameraSnapshot,
    fetchBioXpCameraFrame,
    useBioXpCameraStatus,
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

interface DisplayedFrame {
    url: string;
    sha256: string;
    etag: string;
    source: 'latest' | 'snapshot';
    receivedAtMs: number;
}

export function BioXpCameraPanel({
    connected,
    connectionGeneration,
    mutationEnabled,
}: BioXpCameraPanelProps) {
    const statusQuery = useBioXpCameraStatus(connectionGeneration, connected);
    const ownerRef = useRef<CameraObjectUrlOwner | null>(null);
    const sequenceRef = useRef<{ identity: string; advancedAtMs: number }>({
        identity: '',
        advancedAtMs: Date.now(),
    });
    const mountedRef = useRef(false);
    const [displayedFrame, setDisplayedFrame] = useState<DisplayedFrame | null>(null);
    const [imageError, setImageError] = useState<string | null>(null);
    const [pendingAction, setPendingAction] = useState<'latest' | 'snapshot' | null>(null);
    const [nowMs, setNowMs] = useState(() => Date.now());

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
        const timer = window.setInterval(() => setNowMs(Date.now()), 500);
        return () => window.clearInterval(timer);
    }, []);

    useEffect(() => {
        const status = statusQuery.data;
        if (!status || status.frame_sequence === null) return;
        const identity = `${status.provider_generation}:${status.frame_sequence}`;
        if (sequenceRef.current.identity !== identity) {
            sequenceRef.current = { identity, advancedAtMs: Date.now() };
        }
    }, [statusQuery.data]);

    useEffect(() => {
        ownerRef.current?.clear();
        setDisplayedFrame(null);
        setImageError(null);
        setPendingAction(null);
        sequenceRef.current = { identity: '', advancedAtMs: Date.now() };
    }, [connectionGeneration]);

    const statusError = statusQuery.isError ? bioXpErrorText(statusQuery.error) : null;
    const presentation = useMemo(() => deriveBioXpCameraPresentation({
        status: statusQuery.data ?? null,
        statusReceivedAtMs: statusQuery.dataUpdatedAt || nowMs,
        lastSequenceAdvanceAtMs: sequenceRef.current.advancedAtMs,
        nowMs,
        error: statusError,
    }), [nowMs, statusError, statusQuery.data, statusQuery.dataUpdatedAt]);

    const loadImage = useCallback(async (source: 'latest' | 'snapshot') => {
        const owner = ownerRef.current;
        if (!owner || connectionGeneration === null || !connected) return;
        const token = owner.begin();
        setPendingAction(source);
        setImageError(null);
        try {
            const image = source === 'snapshot'
                ? await captureBioXpCameraSnapshot(connectionGeneration)
                : await fetchBioXpCameraFrame(connectionGeneration);
            if (image.connectionGeneration !== connectionGeneration) {
                throw new Error('Camera image belongs to a superseded BioXP connection generation');
            }
            const url = owner.adopt(token, image.blob);
            if (!url || !owner.isCurrent(token) || !mountedRef.current) return;
            setDisplayedFrame({
                url,
                sha256: image.sha256,
                etag: image.etag,
                source,
                receivedAtMs: Date.now(),
            });
        } catch (error) {
            if (owner.isCurrent(token) && mountedRef.current) setImageError(bioXpErrorText(error));
        } finally {
            if (owner.isCurrent(token) && mountedRef.current) setPendingAction(null);
        }
    }, [connected, connectionGeneration]);

    const labelTone = presentation.label === 'LIVE'
        ? 'border-emerald-600 bg-emerald-950 text-emerald-200'
        : presentation.label === 'STALE'
            ? 'border-amber-600 bg-amber-950 text-amber-200'
            : 'border-red-700 bg-red-950 text-red-200';
    const status = statusQuery.data;
    const frameAge = presentation.effectiveFrameAgeSeconds;
    const imageAgeSeconds = displayedFrame
        ? Math.max(0, nowMs - displayedFrame.receivedAtMs) / 1_000
        : null;

    return (
        <section className="rounded-xl border border-sky-700/60 bg-sky-950/20 p-5" aria-label="BioXP camera observability">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-lg font-semibold">Camera Observability</h2>
                    <p className="mt-1 max-w-3xl text-sm text-slate-300">
                        Read-only camera truth is proxied through the managed BMS connection. Camera freshness never changes command admission.
                    </p>
                </div>
                <span className={`rounded border px-3 py-1 text-sm font-bold ${labelTone}`}>
                    CAMERA {presentation.label}
                </span>
            </div>

            <p className="mt-3 text-sm text-slate-300">{presentation.detail}</p>
            <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-5">
                <div><dt className="text-slate-500">frame age</dt><dd>{frameAge === null ? 'unavailable' : `${frameAge.toFixed(2)} s`}</dd></div>
                <div><dt className="text-slate-500">frame sequence</dt><dd>{status?.frame_sequence ?? 'unavailable'}</dd></div>
                <div><dt className="text-slate-500">provider generation</dt><dd>{status?.provider_generation ?? 'unavailable'}</dd></div>
                <div><dt className="text-slate-500">dropped frames</dt><dd>{status?.dropped_frames ?? 'unavailable'}</dd></div>
                <div><dt className="text-slate-500">connection generation</dt><dd>{connectionGeneration ?? 'disconnected'}</dd></div>
            </dl>

            <div className="mt-4 overflow-hidden rounded border border-slate-800 bg-black">
                {displayedFrame ? (
                    <img
                        src={displayedFrame.url}
                        alt="Validated latest BioXP camera frame"
                        className="mx-auto max-h-[32rem] w-auto max-w-full object-contain"
                    />
                ) : (
                    <div className="flex min-h-64 items-center justify-center p-6 text-sm text-slate-500">
                        Refresh to load a validated frame through the BMS proxy.
                    </div>
                )}
            </div>

            {displayedFrame && (
                <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                    <div><dt className="text-slate-500">display source</dt><dd>{displayedFrame.source}</dd></div>
                    <div><dt className="text-slate-500">display age</dt><dd>{imageAgeSeconds?.toFixed(2)} s</dd></div>
                    <div className="sm:col-span-2"><dt className="text-slate-500">content sha256</dt><dd className="break-all font-mono">{displayedFrame.sha256}</dd></div>
                    <div className="sm:col-span-2"><dt className="text-slate-500">etag</dt><dd className="break-all font-mono">{displayedFrame.etag}</dd></div>
                </dl>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
                <button
                    type="button"
                    disabled={!connected || connectionGeneration === null || pendingAction !== null}
                    onClick={() => void loadImage('latest')}
                    className="rounded bg-sky-700 px-3 py-2 text-sm font-semibold disabled:opacity-40"
                >{pendingAction === 'latest' ? 'Refreshing…' : 'Refresh frame'}</button>
                <button
                    type="button"
                    disabled={!connected || !mutationEnabled || connectionGeneration === null || pendingAction !== null}
                    onClick={() => void loadImage('snapshot')}
                    className="rounded bg-indigo-700 px-3 py-2 text-sm font-semibold disabled:opacity-40"
                >{pendingAction === 'snapshot' ? 'Capturing…' : 'Capture snapshot'}</button>
            </div>
            {!mutationEnabled && (
                <p className="mt-2 text-xs text-amber-300">Snapshot capture is disabled by the existing BMS mutation gate.</p>
            )}
            {status?.detail && <p className="mt-2 text-sm text-amber-300">Camera detail: {status.detail}</p>}
            {imageError && <p role="alert" className="mt-2 text-sm text-red-300">Image request failed: {imageError}</p>}
        </section>
    );
}
