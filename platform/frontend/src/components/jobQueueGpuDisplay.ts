import type { GpuCatalogLike } from './gpuCatalog.js';
import { formatGpuLabel } from './gpuCatalog.js';

export interface QueueGpuDisplayInput {
    displayGpuIds?: number[] | null;
    pinnedGpu?: number | null;
    assignedGpu?: number | null;
    gpuCatalog?: GpuCatalogLike;
}

export interface QueueGpuDisplayState {
    gpuIds: number[];
    isMultiGpu: boolean;
    pinned: boolean;
    badgeText: string;
    detailText: string | null;
    title: string | null;
}

const normalizeGpuIds = (gpuIds?: number[] | null): number[] => {
    if (!Array.isArray(gpuIds)) return [];
    const seen = new Set<number>();
    const resolved: number[] = [];
    for (const rawValue of gpuIds) {
        const gpuId = Number(rawValue);
        if (!Number.isInteger(gpuId) || gpuId < 0 || seen.has(gpuId)) {
            continue;
        }
        seen.add(gpuId);
        resolved.push(gpuId);
    }
    return resolved;
};

export const formatGpuName = (gpuId: number, gpuCatalog?: GpuCatalogLike): string => formatGpuLabel(gpuId, gpuCatalog);

export const formatGpuList = (gpuIds: number[] | null | undefined, gpuCatalog?: GpuCatalogLike): string | null => {
    const normalized = normalizeGpuIds(gpuIds);
    if (normalized.length === 0) return null;
    return normalized.map((gpuId) => formatGpuName(gpuId, gpuCatalog)).join(', ');
};

export const resolveQueueGpuDisplay = ({
    displayGpuIds,
    pinnedGpu,
    assignedGpu,
    gpuCatalog,
}: QueueGpuDisplayInput): QueueGpuDisplayState => {
    const normalizedDisplayGpuIds = normalizeGpuIds(displayGpuIds);
    const gpuIds = normalizedDisplayGpuIds.length > 0
        ? normalizedDisplayGpuIds
        : Number.isInteger(pinnedGpu)
            ? [Number(pinnedGpu)]
            : Number.isInteger(assignedGpu)
                ? [Number(assignedGpu)]
                : [];

    const pinned = gpuIds.length > 1 || Number.isInteger(pinnedGpu);
    if (gpuIds.length === 0) {
        return {
            gpuIds,
            isMultiGpu: false,
            pinned,
            badgeText: 'Auto',
            detailText: null,
            title: null,
        };
    }

    if (gpuIds.length === 1) {
        const label = formatGpuName(gpuIds[0], gpuCatalog);
        return {
            gpuIds,
            isMultiGpu: false,
            pinned,
            badgeText: `${pinned ? '📌 ' : ''}${label}`,
            detailText: null,
            title: label,
        };
    }

    const detailText = formatGpuList(gpuIds, gpuCatalog);
    return {
        gpuIds,
        isMultiGpu: true,
        pinned,
        badgeText: `${pinned ? '📌 ' : ''}${gpuIds.length} GPUs`,
        detailText,
        title: detailText,
    };
};
