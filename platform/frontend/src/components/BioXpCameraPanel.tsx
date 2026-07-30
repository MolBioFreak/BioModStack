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
    const ownerRef = useRef<CameraObjectUrlOwner | null>(null);
    const mountedRef = useRef(false);
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    const [imageError, setImageError] = useState<string | null>(null);
    const [pendingAction, setPendingAction] = useState<'latest' | 'snapshot' | null>(null);

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
    }, [connectionGeneration]);

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
            if (owner.isCurrent(token) && mountedRef.current) setPendingAction(null);
        }
    }, [connected, connectionGeneration]);

    const cameraState = !connected
        ? 'Disconnected'
        : statusQuery.data?.available ? 'Ready' : 'Unavailable';

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

            {statusQuery.data?.detail && <p className="mt-2 text-sm text-amber-300">{statusQuery.data.detail}</p>}
            {statusQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">{bioXpErrorText(statusQuery.error)}</p>}
            {imageError && <p role="alert" className="mt-2 text-sm text-red-300">{imageError}</p>}
        </section>
    );
}
