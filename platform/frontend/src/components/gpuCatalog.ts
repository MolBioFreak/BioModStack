export interface GpuCatalogSource {
    index: number;
    name?: string | null;
    memory_total_mb?: number | null;
    memoryTotalMb?: number | null;
}

export interface GpuCatalogEntry {
    index: number;
    name: string;
    label: string;
    memoryTotalMb: number | null;
}

export type GpuCatalog = Record<number, GpuCatalogEntry>;
export type GpuCatalogLike = GpuCatalog | GpuCatalogEntry[] | null | undefined;

const GPU_PREFIX_PATTERN = /^(?:NVIDIA\s+GeForce\s+|NVIDIA\s+)/i;

export function normalizeGpuName(name: string | null | undefined): string | null {
    const trimmed = name?.replace(/\s+/g, ' ').trim();
    if (!trimmed) return null;
    const withoutVendorPrefix = trimmed.replace(GPU_PREFIX_PATTERN, '').trim();
    return withoutVendorPrefix || trimmed;
}

function normalizeMemoryTotalMb(value: number | null | undefined): number | null {
    if (!Number.isFinite(value) || value == null || value <= 0) {
        return null;
    }
    return Math.round(value);
}

function normalizeCatalogEntry(entry: GpuCatalogSource): Omit<GpuCatalogEntry, 'label'> & { baseLabel: string | null } | null {
    const index = Number(entry.index);
    if (!Number.isInteger(index) || index < 0) return null;

    const baseLabel = normalizeGpuName(entry.name);
    const memoryValue = entry.memory_total_mb ?? entry.memoryTotalMb ?? null;
    return {
        index,
        name: baseLabel ?? `GPU ${index}`,
        baseLabel,
        memoryTotalMb: normalizeMemoryTotalMb(memoryValue),
    };
}

export function buildGpuCatalog(gpus: GpuCatalogSource[] | null | undefined): GpuCatalog {
    const normalized = (gpus ?? [])
        .map(normalizeCatalogEntry)
        .filter((entry): entry is Omit<GpuCatalogEntry, 'label'> & { baseLabel: string | null } => entry !== null)
        .sort((a, b) => a.index - b.index);

    const baseLabelCounts = new Map<string, number>();
    for (const gpu of normalized) {
        if (!gpu.baseLabel) continue;
        baseLabelCounts.set(gpu.baseLabel, (baseLabelCounts.get(gpu.baseLabel) ?? 0) + 1);
    }

    const duplicateOrdinals = new Map<string, number>();
    const catalog: GpuCatalog = {};
    for (const gpu of normalized) {
        let label = gpu.name;
        if (gpu.baseLabel && (baseLabelCounts.get(gpu.baseLabel) ?? 0) > 1) {
            const ordinal = (duplicateOrdinals.get(gpu.baseLabel) ?? 0) + 1;
            duplicateOrdinals.set(gpu.baseLabel, ordinal);
            label = `${gpu.baseLabel} #${ordinal}`;
        }
        catalog[gpu.index] = {
            index: gpu.index,
            name: gpu.name,
            label,
            memoryTotalMb: gpu.memoryTotalMb,
        };
    }
    return catalog;
}

export function listGpuCatalogEntries(catalog: GpuCatalogLike): GpuCatalogEntry[] {
    const entries = Array.isArray(catalog) ? catalog : Object.values(catalog ?? {});
    return entries
        .filter((entry): entry is GpuCatalogEntry => Boolean(entry) && Number.isInteger(entry.index) && entry.index >= 0)
        .sort((a, b) => a.index - b.index);
}

export function getGpuCatalogEntry(gpuId: number, catalog: GpuCatalogLike): GpuCatalogEntry | undefined {
    const normalizedId = Number(gpuId);
    if (!Number.isInteger(normalizedId) || normalizedId < 0) return undefined;
    if (!catalog) return undefined;
    if (Array.isArray(catalog)) {
        return catalog.find((entry) => entry.index === normalizedId);
    }
    return catalog[normalizedId];
}

export function formatGpuLabel(gpuId: number, catalog?: GpuCatalogLike): string {
    const entry = getGpuCatalogEntry(gpuId, catalog);
    return entry?.label || `GPU ${gpuId}`;
}

export function getGpuMemoryTotalMb(gpuId: number, catalog?: GpuCatalogLike): number | null {
    return getGpuCatalogEntry(gpuId, catalog)?.memoryTotalMb ?? null;
}
