import { useEffect } from 'react';
import { QueryClient, QueryObserver, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchPowerControl, fetchFanControl, fetchSchedulerConfig } from './api';
import { jobPollingInterval } from './queryPolling';
import { SHARED_POWER_CONTROL_QUERY_KEY, SHARED_FAN_CONTROL_QUERY_KEY, SHARED_SCHEDULER_CONFIG_QUERY_KEY } from '../components/infraTelemetryQueryKeys';

// The existing shared-query observer pattern, scoped to visible controls.
// One polling owner per resource/client; closed menus are cache readers only.
function controlReader<T>(queryKey: readonly string[], read: () => Promise<T>) {
    const options = {
        queryKey, queryFn: read,
        refetchInterval: (query: Parameters<typeof jobPollingInterval>[1]) => jobPollingInterval(10_000, query),
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    };
    const collectors = new WeakMap<QueryClient, { users: number; unsubscribe: () => void }>();
    return function useControlState(active = true) {
        const client = useQueryClient();
        useEffect(() => {
            if (!active) return;
            let collector = collectors.get(client);
            if (!collector) {
                const observer = new QueryObserver(client, options);
                collector = { users: 0, unsubscribe: observer.subscribe(() => {}) };
                collectors.set(client, collector);
            }
            collector.users++;
            return () => {
                if (--collector.users === 0) {
                    collector.unsubscribe();
                    collectors.delete(client);
                }
            };
        }, [client, active]);
        return useQuery({ queryKey, queryFn: read, enabled: false });
    };
}

export const usePowerControl = controlReader(SHARED_POWER_CONTROL_QUERY_KEY, fetchPowerControl);
export const useFanControl = controlReader(SHARED_FAN_CONTROL_QUERY_KEY, fetchFanControl);
export const useSchedulerConfig = controlReader(SHARED_SCHEDULER_CONFIG_QUERY_KEY, fetchSchedulerConfig);
