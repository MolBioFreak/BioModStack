import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EXECUTION_TARGET_STORAGE_KEY, fetchActiveRemoteGpuTelemetry } from '../lib/api';
import { useSystemStatus } from '../lib/useSystemStatus';
import { buildGpuCatalog, listGpuCatalogEntries } from './gpuCatalog';

const GPU_CATALOG_MAX_AGE_MS = 15_000;
// Match the target telemetry owner's FRESH_SECONDS, not local query freshness.
const REMOTE_GPU_CATALOG_MAX_AGE_MS = 20_000;

interface UseLiveGpuCatalogOptions {
    enabled?: boolean;
    requireFresh?: boolean;
    followExecutionTarget?: boolean;
}

export function useLiveGpuCatalog(options: UseLiveGpuCatalogOptions = {}) {
    const enabled = options.enabled !== false;
    const readTarget = () => options.followExecutionTarget && typeof window !== 'undefined'
        && window.location.pathname === '/submit'
        ? window.sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY) : null;
    const [executionTargetId, setExecutionTargetId] = useState(readTarget);
    useEffect(() => {
        const changed = () => setExecutionTargetId(readTarget());
        window.addEventListener('bms:execution-target-change', changed);
        return () => window.removeEventListener('bms:execution-target-change', changed);
    }, [options.followExecutionTarget]);
    const remoteQuery = useQuery({
        queryKey: ['active-remote-gpu-telemetry', executionTargetId],
        queryFn: () => fetchActiveRemoteGpuTelemetry(undefined, executionTargetId!),
        enabled: enabled && Boolean(executionTargetId),
        placeholderData: undefined,
        refetchInterval: 10_000,
        retry: false,
    });
    const requireFresh = options.requireFresh === true || Boolean(executionTargetId);
    const systemQuery = useSystemStatus(5000, { enabled });
    const [clockMs, setClockMs] = useState(() => Date.now());
    // Demand can resume after its aging timer has been absent for a long time.
    // Apply the unchanged freshness windows on that first render, not one tick later.
    const resumedAtMs = useMemo(() => Date.now(), [enabled, requireFresh]);
    const nowMs = Math.max(clockMs, resumedAtMs);

    useEffect(() => {
        if (!enabled || !requireFresh) return undefined;
        const timer = setInterval(() => setClockMs(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [enabled, requireFresh]);

    const isStale = requireFresh && systemQuery.data !== undefined && (
        systemQuery.dataUpdatedAt <= 0
        || nowMs - systemQuery.dataUpdatedAt > GPU_CATALOG_MAX_AGE_MS
    );
    const isError = systemQuery.isError || systemQuery.isRefetchError;
    const hasAuthoritativeCatalog = systemQuery.data !== undefined && (
        !requireFresh || (
            !isError
            && !isStale
            && !systemQuery.data.data.gpu_error
        )
    );
    const remote = remoteQuery.data?.data;
    const observedAt = Date.parse(remote?.observed_at ?? '');
    const remoteStale = !Number.isFinite(observedAt) || nowMs - observedAt > REMOTE_GPU_CATALOG_MAX_AGE_MS;
    const remoteReady = !remoteQuery.isError && !remoteQuery.isRefetchError && !remoteStale
        && remote?.available && remote.target?.id === executionTargetId
        && remote.target.active && remote.target.state === 'ready';
    const gpuCatalog = useMemo(
        () => buildGpuCatalog(executionTargetId
            ? remoteReady ? remote?.gpus.filter(gpu => gpu.execution_target_id === executionTargetId) ?? [] : []
            : hasAuthoritativeCatalog ? systemQuery.data?.data.gpus ?? [] : []),
        [executionTargetId, remoteReady, remote?.gpus, hasAuthoritativeCatalog, systemQuery.data?.data.gpus],
    );
    const gpuOptions = useMemo(() => listGpuCatalogEntries(gpuCatalog), [gpuCatalog]);

    const telemetryRefusal = executionTargetId
        ? remoteQuery.isError || remoteQuery.isRefetchError
            ? 'Worker GPU telemetry refresh failed. Refresh GPU telemetry before launching Fold-CP.'
            : remoteStale
                ? 'Worker GPU telemetry is stale or unavailable. Refresh GPU telemetry before launching Fold-CP.'
                : !remote?.available || remote.target?.id !== executionTargetId || !remote.target.active || remote.target.state !== 'ready'
                    ? 'Selected worker is not ready for GPU placement. Refresh GPU telemetry before launching Fold-CP.'
                    : null
        : requireFresh && (isError || isStale || !hasAuthoritativeCatalog)
            ? 'Local GPU telemetry is stale or unavailable. Refresh GPU telemetry before launching Fold-CP.'
            : null;

    return {
        gpuCatalog,
        gpuOptions,
        executionTargetId,
        telemetryRefusal,
        refreshGpuTelemetry: () => executionTargetId ? remoteQuery.refetch() : systemQuery.refetch(),
        isLoading: executionTargetId ? remoteQuery.isPending : requireFresh ? systemQuery.isPending : systemQuery.isLoading,
        isError: executionTargetId ? !remoteReady : isError || (requireFresh && Boolean(systemQuery.data?.data.gpu_error)),
        isStale: executionTargetId ? remoteStale : isStale,
    };
}
