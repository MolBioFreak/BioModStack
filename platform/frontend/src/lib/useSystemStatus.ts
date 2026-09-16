import { useEffect } from 'react';
import { QueryClient, QueryObserver, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchSystemStatus } from './api';
import { jobPollingInterval } from './queryPolling';

const systemKey = ['system'];
const options = (interval: number) => ({
    queryKey: systemKey,
    queryFn: fetchSystemStatus,
    refetchInterval: (query: Parameters<typeof jobPollingInterval>[1]) => jobPollingInterval(interval, query),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
});

// One active TanStack observer per client owns polling, retries and invalidation.
// Component observers only read the existing query; no second payload cache/timer.
const collectors = new WeakMap<QueryClient, {
    intervals: Map<symbol, number>;
    observer: QueryObserver<Awaited<ReturnType<typeof fetchSystemStatus>>, Error, Awaited<ReturnType<typeof fetchSystemStatus>>, Awaited<ReturnType<typeof fetchSystemStatus>>, string[]>;
    unsubscribe: () => void;
}>();

export function useSystemStatus(intervalMs = 5000) {
    const client = useQueryClient();
    useEffect(() => {
        const token = Symbol();
        let collector = collectors.get(client);
        if (!collector) {
            const observer = new QueryObserver(client, options(intervalMs));
            collector = { intervals: new Map(), observer, unsubscribe: observer.subscribe(() => {}) };
            collectors.set(client, collector);
        }
        collector.intervals.set(token, intervalMs);
        collector.observer.setOptions(options(Math.min(...collector.intervals.values())));
        return () => {
            collector.intervals.delete(token);
            if (!collector.intervals.size) {
                collector.unsubscribe();
                collectors.delete(client);
            } else {
                collector.observer.setOptions(options(Math.min(...collector.intervals.values())));
            }
        };
    }, [client, intervalMs]);
    return useQuery({ queryKey: systemKey, queryFn: fetchSystemStatus, enabled: false });
}
