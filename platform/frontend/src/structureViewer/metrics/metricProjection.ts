import type { MolstarResidueMetricLayer, MolstarRgbColor } from '../../lib/molstar-metrics.js';
import type { AtomRef, ResidueRef } from '../contracts/structureIdentity.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from '../contracts/viewerResults.js';
import type { MetricLayer, MetricValue } from './metricContracts.js';

export interface MetricProjectionOptions {
    readonly range?: readonly [number, number];
    readonly missingColor?: MolstarRgbColor;
}

const parseColor = (value: string | MolstarRgbColor): MolstarRgbColor | null => {
    if (typeof value !== 'string') return value;
    const match = /^#([0-9a-f]{6})$/i.exec(value);
    if (!match) return null;
    const packed = Number.parseInt(match[1]!, 16);
    return { r: (packed >> 16) & 255, g: (packed >> 8) & 255, b: packed & 255 };
};
const mix = (low: MolstarRgbColor, high: MolstarRgbColor, fraction: number): MolstarRgbColor => ({
    r: Math.round(low.r + ((high.r - low.r) * fraction)),
    g: Math.round(low.g + ((high.g - low.g) * fraction)),
    b: Math.round(low.b + ((high.b - low.b) * fraction)),
});
const paletteColor = (colors: readonly MolstarRgbColor[], fraction: number): MolstarRgbColor => {
    if (colors.length === 1) return colors[0]!;
    const scaled = Math.max(0, Math.min(1, fraction)) * (colors.length - 1);
    const lower = Math.floor(scaled);
    return mix(colors[lower]!, colors[Math.min(colors.length - 1, lower + 1)]!, scaled - lower);
};
const finiteValues = (values: readonly MetricValue<ResidueRef>[]): number[] => values.flatMap((entry) => (
    typeof entry.value === 'number' && Number.isFinite(entry.value) && entry.missingness === undefined ? [entry.value] : []
));

/** Display precision only: native values and provenance remain in the metric layer. */
const metricTooltip = (layer: MetricLayer, value: number, identity: ResidueRef | AtomRef): string => {
    const nativePlddt = ['native-plddt', 'native-plddt-atoms'].includes(layer.descriptor.id ?? '') && layer.descriptor.units === 'fraction';
    const rendered = nativePlddt ? `${(value * 100).toFixed(1)} /100`
        : `${Number(value.toPrecision(4))}${layer.descriptor.units ? ` ${layer.descriptor.units}` : ''}`;
    const author = identity.authAsymId !== undefined && identity.authSeqId !== undefined;
    const chain = author ? identity.authAsymId : identity.labelAsymId;
    const number = author ? identity.authSeqId : identity.labelSeqId;
    const residue = `${identity.componentId ?? ''}${number ?? ''}${author ? identity.insertionCode ?? '' : ''}`;
    const atom = (identity as AtomRef).authAtomId ?? (identity as AtomRef).labelAtomId;
    const location = [chain, residue || undefined, atom, identity.altLoc ? `alt ${identity.altLoc}` : undefined]
        .filter(Boolean).join(' · ');
    return `${location ? `${location} — ` : ''}${layer.descriptor.label}: ${rendered}`;
};

export const projectAtomMetricLayer = (layer: MetricLayer) => {
    if (layer.descriptor.dimension !== 'atom-scalar' || layer.descriptor.projectionPolicy !== 'direct') return [];
    const domain = layer.descriptor.palette?.domain ?? layer.descriptor.valueRange;
    if (!domain) return [];
    const palette = (layer.descriptor.palette?.colors ?? ['#2563eb','#f8fafc','#dc2626']).map(parseColor).filter((c): c is MolstarRgbColor => c !== null);
    if (!palette.length) return [];
    return (layer.values as readonly MetricValue<AtomRef>[]).flatMap(entry => {
        if (typeof entry.value !== 'number' || !Number.isFinite(entry.value) || entry.missingness !== undefined) return [];
        return [{identity:entry.identity, color: (entry.displayColor ? parseColor(entry.displayColor) : null) ?? paletteColor(palette, (entry.value-domain[0])/(domain[1]-domain[0])),
            tooltip: metricTooltip(layer, entry.value, entry.identity)}];
    });
};

export const projectResidueMetricLayer = (layer: MetricLayer, options: MetricProjectionOptions = {}): ViewerResult<MolstarResidueMetricLayer> => {
    if (layer.descriptor.dimension !== 'residue-scalar' || layer.descriptor.projectionPolicy !== 'direct') {
        return viewerUnsupported(`Metric ${layer.descriptor.id} does not declare direct residue projection`, 'metric-projection');
    }
    const residueValues = layer.values as readonly MetricValue<ResidueRef>[];
    const numeric = finiteValues(residueValues);
    if (numeric.length === 0) return viewerUnsupported('Residue metric has no finite values to project', 'metric-projection');
    const declared = options.range ?? layer.descriptor.palette?.domain ?? layer.descriptor.valueRange;
    if (!declared && layer.descriptor.normalization === 'none') {
        return viewerUnsupported('Unnormalized residue projection requires an explicit palette domain or value range', 'metric-projection');
    }
    const min = declared?.[0] ?? Math.min(...numeric);
    const max = declared?.[1] ?? Math.max(...numeric);
    const span = max > min ? max - min : 1;
    const palette = (layer.descriptor.palette?.colors ?? ['#2563eb', '#f8fafc', '#dc2626'])
        .map(parseColor).filter((color): color is MolstarRgbColor => color !== null);
    if (palette.length === 0) return viewerUnsupported('Metric palette has no valid #RRGGBB colors', 'metric-projection');
    const values = residueValues.flatMap((entry) => {
        if (typeof entry.value !== 'number' || !Number.isFinite(entry.value) || entry.missingness !== undefined) return [];
        const explicit = entry.displayColor ? parseColor(entry.displayColor) : null;
        const fraction = Math.max(0, Math.min(1, (entry.value - min) / span));
        return [{
            residue: {
                documentId: entry.identity.documentId,
                labelAsymId: entry.identity.labelAsymId, authAsymId: entry.identity.authAsymId,
                labelSeqId: entry.identity.labelSeqId, authSeqId: entry.identity.authSeqId,
                insertionCode: entry.identity.insertionCode, entityId: entry.identity.entityId,
                instanceId: entry.identity.operatorInstanceId ?? entry.identity.sourceInstanceId,
            },
            value: entry.value,
            color: explicit ?? paletteColor(palette, fraction),
            tooltip: metricTooltip(layer, entry.value, entry.identity),
        }];
    });
    const missing = parseColor(options.missingColor ?? layer.descriptor.palette?.missingColor ?? '#444444') ?? { r: 68, g: 68, b: 68 };
    return viewerOk({
        scope: 'residue-scalar',
        descriptor: {
            id: layer.descriptor.id, label: layer.descriptor.label, semanticType: 'custom',
            units: layer.descriptor.units, direction: layer.descriptor.direction,
            source: layer.descriptor.provenance.source, range: [min, max],
            provenance: { ...layer.descriptor.provenance.parameters },
        },
        points: values,
        nonSelectedColor: missing,
    });
};
