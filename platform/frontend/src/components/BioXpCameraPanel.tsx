import { useCallback, useEffect, useRef, useState } from 'react';

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

export function BioXpCameraPanel({
    connected,
    connectionGeneration,
    mutationEnabled,
}: BioXpCameraPanelProps) {
    const statusQuery = useBioXpCameraStatus(connectionGeneration, connected);
    const refetchStatus = statusQuery.refetch;
    const ownerRef = useRef<CameraObjectUrlOwner | null>(null);
    const mountedRef = useRef(false);
    const statusReceivedAtRef = useRef(Date.now());
    const lastSequenceRef = useRef<number | null>(null);
    const lastSequenceAdvanceAtRef = useRef(Date.now());
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    const [imageError, setImageError] = useState<string | null>(null);
    const [pendingAction, setPendingAction] = useState<'latest' | 'snapshot' | null>(null);
    const [presentationNowMs, setPresentationNowMs] = useState(() => Date.now());
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
        setPendingAction(null);
        const now = Date.now();
        statusReceivedAtRef.current = now;
        lastSequenceRef.current = null;
        lastSequenceAdvanceAtRef.current = now;
        setPresentationNowMs(now);
        bumpPresentationRevision((revision) => revision + 1);
    }, [connectionGeneration]);

    useEffect(() => {
        if (!statusQuery.data) return;
        const receivedAt = statusQuery.dataUpdatedAt || Date.now();
        statusReceivedAtRef.current = receivedAt;
        if (statusQuery.data.frame_sequence !== lastSequenceRef.current) {
            lastSequenceRef.current = statusQuery.data.frame_sequence;
            lastSequenceAdvanceAtRef.current = receivedAt;
        }
        setPresentationNowMs(Date.now());
        bumpPresentationRevision((revision) => revision + 1);
    }, [statusQuery.data, statusQuery.dataUpdatedAt]);

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
                throw new Error('Camera frame belongs to a previous connection');
            }
            const nextUrl = owner.adopt(token, image.blob);
            if (nextUrl && owner.isCurrent(token) && mountedRef.current) setImageUrl(nextUrl);
        } catch (error) {
            if (owner.isCurrent(token) && mountedRef.current) setImageError(bioXpErrorText(error));
        } finally {
            if (owner.isCurrent(token) && mountedRef.current) {
                await refetchStatus().catch(() => undefined);
                if (owner.isCurrent(token) && mountedRef.current) setPendingAction(null);
            }
        }
    }, [connected, connectionGeneration, refetchStatus]);

    const cameraStatus = statusQuery.isError ? undefined : statusQuery.data;
    useEffect(() => {
        if (!connected
            || !cameraStatus
            || !cameraStatus.available
            || cameraStatus.state !== 'live'
            || cameraStatus.frame_age_seconds === null) return;
        const budgetMs = cameraStatus.freshness_budget_seconds * 1_000;
        const effectiveAgeMs = cameraStatus.frame_age_seconds * 1_000
            + Math.max(0, presentationNowMs - statusReceivedAtRef.current);
        const frameRemainingMs = budgetMs - effectiveAgeMs;
        const sequenceRemainingMs = budgetMs
            - Math.max(0, presentationNowMs - lastSequenceAdvanceAtRef.current);
        const expiryDelayMs = Math.min(frameRemainingMs, sequenceRemainingMs);
        if (expiryDelayMs <= 0) return;
        const timer = window.setTimeout(
            () => {
                setPresentationNowMs(Date.now());
                bumpPresentationRevision((revision) => revision + 1);
            },
            Math.ceil(expiryDelayMs) + 1,
        );
        return () => window.clearTimeout(timer);
    }, [cameraStatus, connected, presentationNowMs]);
    const presentation = deriveBioXpCameraPresentation({
        status: cameraStatus ?? null,
        statusReceivedAtMs: statusReceivedAtRef.current,
        lastSequenceAdvanceAtMs: lastSequenceAdvanceAtRef.current,
        nowMs: presentationNowMs,
        error: statusQuery.isError ? bioXpErrorText(statusQuery.error) : null,
    });
    const cameraState = !connected
        ? 'Disconnected'
        : presentation.label === 'LIVE' ? 'Ready'
            : presentation.label === 'STALE' ? 'Stale' : 'Unavailable';

    return (
        <section className="rounded-xl border border-sky-800/70 bg-sky-950/20 p-4">
            <div className="flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">Camera</h2>
                <span className="text-sm text-slate-400">{cameraState}</span>
            </div>

            <div className="mt-3 flex min-h-64 items-center justify-center overflow-hidden rounded-lg border border-slate-800 bg-black">
                {imageUrl
                    ? <img src={imageUrl} alt="BioXP camera" className="max-h-[32rem] max-w-full object-contain" />
                    : <span className="text-sm text-slate-500">No frame loaded</span>}
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
                <button
                    type="button"
                    disabled={!connected || connectionGeneration === null || pendingAction !== null}
                    onClick={() => void loadImage('latest')}
                    className="rounded bg-sky-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                >{pendingAction === 'latest' ? 'Loading…' : 'Refresh'}</button>
                <button
                    type="button"
                    disabled={!connected || !mutationEnabled || connectionGeneration === null || pendingAction !== null}
                    onClick={() => void loadImage('snapshot')}
                    className="rounded bg-indigo-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                >{pendingAction === 'snapshot' ? 'Capturing…' : 'Capture'}</button>
            </div>

            {cameraStatus?.detail && <p className="mt-2 text-sm text-amber-300">{cameraStatus.detail}</p>}
            {statusQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(statusQuery.error)}</p>}
            {imageError && <p role="alert" className="mt-2 text-sm text-red-300">{imageError}</p>}
        </section>
    );
}
