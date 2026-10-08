import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchSystemStatus } from '../lib/api';
import { buildGpuCatalog, listGpuCatalogEntries } from './gpuCatalog';

const GPU_CATALOG_MAX_AGE_MS = 15_000;

interface UseLiveGpuCatalogOptions {
    requireFresh?: boolean;
}

export function useLiveGpuCatalog(options: UseLiveGpuCatalogOptions = {}) {
    const requireFr...[truncated]        queryKey: ['system'],
        queryFn: fetchSystemStatus,
        refetchInterval: 5000,
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });
    const [nowMs, setNowMs] = useState(() => Date.now());

    useEffect(() => {
        if (!requireFresh) return undefined;
        const timer = setInterval(() => setNowMs(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [requireFresh]);

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
    const gpuCatalog = useMemo(
        () => buildGpuCatalog(hasAuthoritativeCatalog ? systemQuery.data?.data.gpus ?? [] : []),
        [hasAuthoritativeCatalog, systemQuery.data?.data.gpus],
    );
    const gpuOptions = useMemo(() => listGpuCatalogEntries(gpuCatalog), [gpuCatalog]);

    return {
        gpuCatalog,
        gpuOptions,
        isLoading: requireFresh ? systemQuery.isPending : systemQuery.isLoading,
        isError: isError || (requireFresh && Boolean(systemQuery.data?.data.gpu_error)),
        isStale,
    };
}
