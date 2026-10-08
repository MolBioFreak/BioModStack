export interface MolstarRgbColor {
    r: number;
    g: number;
    b: number;
}

/**
 * Canonical residue identity retained at the application boundary.
 *
 * The BMS direct Mol* query contract supports both label and author numbering,
 * including author insertion codes. Keeping both namespaces prevents the old
 * `A45`/`A:45` string-key ambiguity and makes conversion explicit.
 */
export interface MolstarResidueIdentity {
    documentId?: string;
    labelAsymId?: string;
    authAsymId?: string;
    labelSeqId?: number;
    authSeqId?: number;
    insertionCode?: string | null;
    entityId?: string;
    instanceId?: string;
}

export type ResidueMetricSemanticType = 'confidence' | 'energy' | 'distance' | 'frustration' | 'custom';
export type ResidueMetricDirection = 'higher_is_better' | 'lower_is_better' | 'neutral';

export interface ResidueMetricDescriptor {
    id: string;
    label: string;
    semanticType: ResidueMetricSemanticType;
    units: string | null;
    direction: ResidueMetricDirection;
    source: string;
    range?: readonly [number, number];
    provenance?: Record<string, string | number | boolean | null>;
}

export interface ResidueMetricPoint {
    residue: MolstarResidueIdentity;
    value: number;
    color: MolstarRgbColor;
    tooltip?: string;
}

export interface MolstarResidueMetricLayer {
    scope: 'residue-scalar';
    descriptor: ResidueMetricDescriptor;
    points: ResidueMetricPoint[];
    nonSelectedColor?: MolstarRgbColor;
}

export interface PdbeMolstarQueryParam {
    entity_id?: string;
    struct_asym_id?: string;
    auth_asym_id?: string;
    residue_number?: number;
    auth_residue_number?: number;
    auth_ins_code_id?: string;
    instance_id?: string;
}

export interface PdbeMolstarColorSelection extends PdbeMolstarQueryParam {
    color: MolstarRgbColor;
}

export interface PdbeMolstarTooltipSelection extends PdbeMolstarQueryParam {
    tooltip: string;
}

export interface MolstarMetricAdapterResult {
    colorSelections: PdbeMolstarColorSelection[];
    tooltipSelections: PdbeMolstarTooltipSelection[];
    rejected: Array<{ point: ResidueMetricPoint; reason: string }>;
}

const finiteInteger = (value: unknown): value is number => (
    typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value)
);

const cleanIdentifier = (value: string | null | undefined): string | undefined => {
    const cleaned = value?.trim();
    return cleaned ? cleaned : undefined;
};

export interface ExplicitResidueColorMapInput {
    descriptor: ResidueMetricDescriptor;
    colors: ReadonlyMap<string, MolstarRgbColor>;
    values: ReadonlyMap<string, number>;
    nonSelectedColor?: MolstarRgbColor;
}

/** Convert only unambiguous `label_asym_id:label_seq_id` keys. */
export const buildMetricLayerFromExplicitMaps = (
    input: ExplicitResidueColorMapInput,
): MolstarResidueMetricLayer => {
    const points: ResidueMetricPoint[] = [];
    for (const [key, color] of input.colors) {
        const match = key.match(/^([^:]+):(-?\d+)$/);
        const value = input.values.get(key);
        if (!match || !Number.isFinite(value)) continue;
        points.push({
            residue: { labelAsymId: match[1], labelSeqId: Number(match[2]) },
            value: value as number,
            color,
        });
    }
    return createResidueMetricLayer({
        descriptor: input.descriptor,
        points,
        nonSelectedColor: input.nonSelectedColor,
    });
};

export const canonicalResidueKey = (residue: MolstarResidueIdentity): string => {
    const fields = [
        ['entity', cleanIdentifier(residue.entityId) ?? ''],
        ['label_asym', cleanIdentifier(residue.labelAsymId) ?? ''],
        ['auth_asym', cleanIdentifier(residue.authAsymId) ?? ''],
        ['label_seq', finiteInteger(residue.labelSeqId) ? String(residue.labelSeqId) : ''],
        ['auth_seq', finiteInteger(residue.authSeqId) ? String(residue.authSeqId) : ''],
        ['ins', cleanIdentifier(residue.insertionCode) ?? ''],
        ['instance', cleanIdentifier(residue.instanceId) ?? ''],
    ];
    return fields.map(([name, value]) => `${name}=${encodeURIComponent(value)}`).join('|');
};

const residueQuery = (residue: MolstarResidueIdentity): PdbeMolstarQueryParam | null => {
    const structAsymId = cleanIdentifier(residue.labelAsymId);
    const authAsymId = cleanIdentifier(residue.authAsymId);
    const entityId = cleanIdentifier(residue.entityId);
    const insertionCode = cleanIdentifier(residue.insertionCode);
    const hasLabelNumber = finiteInteger(residue.labelSeqId);
    const hasAuthorNumber = finiteInteger(residue.authSeqId);

    if (!structAsymId && !authAsymId) return null;
    if (!hasLabelNumber && !hasAuthorNumber) return null;

    const query: PdbeMolstarQueryParam = {
        ...(entityId ? { entity_id: entityId } : {}),
        ...(structAsymId ? { struct_asym_id: structAsymId } : {}),
        ...(authAsymId ? { auth_asym_id: authAsymId } : {}),
        ...(hasLabelNumber ? { residue_number: residue.labelSeqId } : {}),
        ...(hasAuthorNumber ? { auth_residue_number: residue.authSeqId } : {}),
        ...(hasAuthorNumber && insertionCode ? { auth_ins_code_id: insertionCode } : {}),
    };
    return query;
};

const metricTooltip = (descriptor: ResidueMetricDescriptor, point: ResidueMetricPoint): string => {
    if (point.tooltip?.trim()) return point.tooltip.trim();
    const units = descriptor.units ? ` ${descriptor.units}` : '';
    const source = descriptor.source.trim() ? ` · ${descriptor.source.trim()}` : '';
    return `${descriptor.label}: ${point.value}${units}${source}`;
};

/** Adapt a canonical scalar-residue metric layer to the BMS direct Mol* query contract. */
export const adaptResidueMetricLayer = (layer: MolstarResidueMetricLayer): MolstarMetricAdapterResult => {
    const colorSelections: PdbeMolstarColorSelection[] = [];
    const tooltipSelections: PdbeMolstarTooltipSelection[] = [];
    const rejected: MolstarMetricAdapterResult['rejected'] = [];
    const seen = new Set<string>();

    for (const point of layer.points) {
        if (!Number.isFinite(point.value)) {
            rejected.push({ point, reason: 'metric value is not finite' });
            continue;
        }
        if (cleanIdentifier(point.residue.instanceId)) {
            rejected.push({ point, reason: 'operator/instance identity is not supported by the direct adapter' });
            continue;
        }
        const query = residueQuery(point.residue);
        if (!query) {
            rejected.push({ point, reason: 'residue lacks a chain identifier or supported residue number' });
            continue;
        }
        const key = canonicalResidueKey(point.residue);
        if (seen.has(key)) {
            rejected.push({ point, reason: `duplicate canonical residue identity: ${key}` });
            continue;
        }
        seen.add(key);
        colorSelections.push({ ...query, color: point.color });
        tooltipSelections.push({ ...query, tooltip: metricTooltip(layer.descriptor, point) });
    }

    return { colorSelections, tooltipSelections, rejected };
};

export interface ChainResidueMetricSeries {
    labelAsymId?: string;
    authAsymId?: string;
    labelSeqIds?: number[];
    authSeqIds?: number[];
    insertionCodes?: Array<string | null>;
    values: number[];
}

/**
 * Build a canonical layer from chain-aware backend arrays. Numbering is never
 * synthesized here: points without an explicit label or author residue number
 * are rejected by the adapter instead of being colored onto the wrong chain.
 */
export const createResidueMetricLayer = (input: {
    descriptor: ResidueMetricDescriptor;
    points: ResidueMetricPoint[];
    nonSelectedColor?: MolstarRgbColor;
}): MolstarResidueMetricLayer => {
    const seen = new Set<string>();
    const points = input.points
        .filter((point) => Number.isFinite(point.value))
        .filter((point) => {
            const key = canonicalResidueKey(point.residue);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    return {
        scope: 'residue-scalar',
        descriptor: input.descriptor,
        points,
        nonSelectedColor: input.nonSelectedColor,
    };
};

export const buildResidueMetricLayer = (input: {
    descriptor: ResidueMetricDescriptor;
    chains: Record<string, ChainResidueMetricSeries>;
    colorForValue: (value: number) => MolstarRgbColor;
    nonSelectedColor?: MolstarRgbColor;
}): MolstarResidueMetricLayer => {
    const points: ResidueMetricPoint[] = [];
    for (const [fallbackChainId, series] of Object.entries(input.chains)) {
        const count = series.values.length;
        for (let index = 0; index < count; index += 1) {
            const value = series.values[index];
            if (!Number.isFinite(value)) continue;
            points.push({
                residue: {
                    labelAsymId: cleanIdentifier(series.labelAsymId)
                        ?? (cleanIdentifier(series.authAsymId) ? undefined : cleanIdentifier(fallbackChainId)),
                    authAsymId: cleanIdentifier(series.authAsymId),
                    labelSeqId: series.labelSeqIds?.[index],
                    authSeqId: series.authSeqIds?.[index],
                    insertionCode: series.insertionCodes?.[index] ?? null,
                },
                value,
                color: input.colorForValue(value),
            });
        }
    }
    return {
        scope: 'residue-scalar',
        descriptor: input.descriptor,
        points,
        ...(input.nonSelectedColor ? { nonSelectedColor: input.nonSelectedColor } : {}),
    };
};
