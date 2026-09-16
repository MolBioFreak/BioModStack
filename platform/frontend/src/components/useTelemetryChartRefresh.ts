import { useEffect, useState } from 'react';
import { focusManager, onlineManager } from '@tanstack/react-query';
import { scheduleTelemetryRefresh } from './infraTelemetryHistory';

interface RefreshQuery {
    fetchStatus: 'fetching' | 'paused' | 'idle';
    dataUpdatedAt: number;
    errorUpdatedAt: number;
    isError: boolean;
    failureCount: number;
    refetch: () => Promise<unknown>;
}

export function useTelemetryChartRefresh(
    query: RefreshQuery,
    enabled: boolean,
    intervalMs: number,
    queryIdentity: number,
): string {
    const [focused, setFocused] = useState(() => focusManager.isFocused());
    const [online, setOnline] = useState(() => onlineManager.isOnline());
    const [seconds, setSeconds] = useState<number | null>(null);
    useEffect(() => focusManager.subscribe(setFocused), []);
    useEffect(() => onlineManager.subscribe(setOnline), []);
    const { fetchStatus, dataUpdatedAt, errorUpdatedAt, refetch } = query;
    const paused = !focused || !online || fetchStatus === 'paused';

    useEffect(() => {
        setSeconds(null);
        if (!enabled || paused || fetchStatus !== 'idle') return;
        return scheduleTelemetryRefresh(intervalMs, () => {
            // Recheck the managers at dispatch too: a hidden/offline event may
            // arrive before React has committed effect cleanup.
            if (focusManager.isFocused() && onlineManager.isOnline()) void refetch();
        }, setSeconds);
    }, [enabled, intervalMs, queryIdentity, paused, fetchStatus, dataUpdatedAt, errorUpdatedAt, refetch]);

    if (!enabled) return '';
    if (paused) return 'Chart refresh paused';
    if (fetchStatus === 'fetching') return query.failureCount > 0 ? 'Retrying chart refresh…' : 'Updating chart…';
    if (query.isError) return seconds == null || seconds === 0
        ? 'Chart refresh failed · waiting to retry'
        : `Chart refresh failed · retry in ${seconds}s`;
    return seconds == null || seconds === 0 ? 'Chart refresh due' : `Next chart refresh in ${seconds}s`;
}
