import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { fetchSystemStatus } from '../lib/api';
import { buildGpuCatalog, listGpuCatalogEntries } from './gpuCatalog';

export function useLiveGpuCatalog() {
    const systemQuery = useQuery({
        queryKey: ['system'],
        queryFn: fetchSystemStatus,
        refetchInterval: 5000,
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    const gpuCatalog = useMemo(
        () => buildGpuCatalog(systemQuery.data?.data.gpus ?? []),
        [systemQuery.data?.data.gpus],
    );
    const gpuOptions = useMemo(() => listGpuCatalogEntries(gpuCatalog), [gpuCatalog]);

    return {
        gpuCatalog,
        gpuOptions,
        isLoading: systemQuery.isLoading,
        isError: systemQuery.isError,
    };
}
