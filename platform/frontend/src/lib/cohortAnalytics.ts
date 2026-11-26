import type { Data } from 'plotly.js';

export interface CohortRow {
    id: string;
    label: string;
    values: Record<string, unknown>;
}

export const isFiniteMetric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Only a complete native residue-fraction partition can be shown as a composition pie. */
export function secondaryStructureComposition(row: CohortRow | undefined): number[] | null {
    if (!row) return null;
    const values = ['coil_percent', 'helix_percent', 'strand_percent'].map(key => row.values[key]);
    if (!values.every(value => isFiniteMetric(value) && value >= 0 && value <= 1)) return null;
    const fractions = values as number[];
    const total = fractions.reduce((acc, value) => acc + value, 0);
    return Math.abs(total - 1) <= 0.02 ? fractions : null;
}

/** Native appearance order across the entire supplied cohort; never infer a schema from row one. */
export function metricKeys(rows: CohortRow[]): string[] {
    return [...new Set(rows.flatMap(row => Object.keys(row.values)))];
}

export function numericMetricKeys(rows: CohortRow[]): string[] {
    const numeric = new Set<string>();
    for (const row of rows) for (const [key, value] of Object.entries(row.values)) if (isFiniteMetric(value)) numeric.add(key);
    return metricKeys(rows).filter(key => numeric.has(key));
}

export interface MetricObservationCounts {
    observed: number;
    missing: number;
    nulls: number;
    nonNumeric: number;
}

/** Non-statistical presence tally for display alongside stored Stats Toolkit summaries. */
export function countMetricObservations(rows: CohortRow[], key: string): MetricObservationCounts {
    const counts: MetricObservationCounts = { observed: 0, missing: 0, nulls: 0, nonNumeric: 0 };
    for (const row of rows) {
        const value = Object.prototype.hasOwnProperty.call(row.values, key) ? row.values[key] : undefined;
        if (value === undefined) counts.missing++;
        else if (value === null) counts.nulls++;
        else if (isFiniteMetric(value)) counts.observed++;
        else counts.nonNumeric++;
    }
    return counts;
}

export interface MetricSummary {
    observed: number;
    missing: number;
    nulls: number;
    nonNumeric: number;
    min: number | null;
    max: number | null;
    mean: number | null;
    median: number | null;
    q1: number | null;
    q3: number | null;
    stdDev: number | null;
    histogramBins?: number[];
    histogramCounts?: number[];
}

export interface StoredToolkitColumn {
    count?: number | null;
    min?: number | null;
    max?: number | null;
    mean?: number | null;
    avg?: number | null;
    median?: number | null;
    q1?: number | null;
    q3?: number | null;
    std?: number | null;
    std_dev?: number | null;
    histogram?: { edges?: number[]; counts?: number[] } | null;
    histogram_bins?: number[] | null;
    histogram_counts?: number[] | null;
}

/** Adapt a stored Stats Toolkit descriptive column or MetricDistribution without local math. */
export function storedMetricSummary(
    stored: StoredToolkitColumn | null | undefined,
    rows: CohortRow[],
    key: string,
): MetricSummary {
    const counts = countMetricObservations(rows, key);
    if (!stored) {
        return {
            ...counts,
            min: null,
            max: null,
            mean: null,
            median: null,
            q1: null,
            q3: null,
            stdDev: null,
        };
    }
    const num = (value: unknown): number | null => (isFiniteMetric(value) ? value : null);
    const bins = Array.isArray(stored.histogram_bins)
        ? stored.histogram_bins.filter(isFiniteMetric)
        : Array.isArray(stored.histogram?.edges)
            ? stored.histogram.edges.filter(isFiniteMetric)
            : undefined;
    const histCounts = Array.isArray(stored.histogram_counts)
        ? stored.histogram_counts.filter(isFiniteMetric)
        : Array.isArray(stored.histogram?.counts)
            ? stored.histogram.counts.filter(isFiniteMetric)
            : undefined;
    return {
        ...counts,
        observed: typeof stored.count === 'number' && Number.isFinite(stored.count) ? stored.count : counts.observed,
        min: num(stored.min),
        max: num(stored.max),
        mean: num(stored.mean ?? stored.avg),
        median: num(stored.median),
        q1: num(stored.q1),
        q3: num(stored.q3),
        stdDev: num(stored.std ?? stored.std_dev),
        histogramBins: bins,
        histogramCounts: histCounts,
    };
}

/** Render a Plotly bar trace directly from stored Stats Toolkit histogram edges and counts. */
export function histogramTraceFromStored(
    bins: number[] | undefined,
    counts: number[] | undefined,
    options: { label: string; color: string; lineColor: string; opacity?: number; hoverUnit?: string },
): Data[] {
    if (!bins || !counts || counts.length === 0 || bins.length < counts.length + 1) {
        return [];
    }
    const centers: number[] = [];
    const widths: number[] = [];
    const ranges: string[] = [];
    for (let i = 0; i < counts.length; i += 1) {
        const lo = bins[i];
        const hi = bins[i + 1];
        centers.push((lo + hi) / 2);
        const span = hi - lo;
        widths.push(span > 0 ? span : 1);
        ranges.push(`${formatMetric(lo)} – ${formatMetric(hi)}`);
    }
    const countLabel = options.hoverUnit ?? 'Records';
    return [{
        type: 'bar',
        x: centers,
        y: counts,
        width: widths,
        customdata: ranges,
        marker: {
            color: options.color,
            line: { color: options.lineColor, width: 1 },
            ...(options.opacity !== undefined ? { opacity: options.opacity } : {}),
        },
        hovertemplate: `${options.label}: %{customdata}<br>${countLabel}: %{y}<extra></extra>`,
    } as Data];
}

export interface CorrelationSummary {
    pairs: number;
    r: number | null;
    reason: string | null;
}

const LABELS: Record<string, string> = {
    seq_length: 'Sequence length', dsasa: 'dSASA', num_ca_ca_clashes: 'CA–CA clashes',
    plddt: 'pLDDT', iptm: 'iPTM', ptm: 'pTM', pae: 'PAE', gpde: 'gPDE', ranking_score: 'Native ranking score',
    plddt_overall: 'Overall pLDDT', plddt_binder: 'Binder pLDDT', plddt_mean: 'Mean pLDDT',
    complex_plddt: 'Complex pLDDT', design_ptm: 'Design pTM', filter_rmsd: 'Filter RMSD',
    pae_overall: 'Overall PAE', pae_interaction: 'Interaction PAE',
    rmsd_overall: 'Overall RMSD', rmsd_binder: 'Binder RMSD',
    ppiflow_sequence_identity: 'PPIFlow sequence identity',
    ppiflow_clash_count_ca: 'PPIFlow CA clash count',
    maturation_delta_interface: 'Interface score change',
    maturation_selected_delta_interface: 'Selected interface score change',
    maturation_interface_score: 'Interface score', maturation_selected_interface_score: 'Selected interface score',
    maturation_rmsd: 'Maturation RMSD', maturation_selected_rmsd: 'Selected maturation RMSD',
    maturation_nonselected_rmsd: 'Nonselected RMSD',
    ppiflow_interface_score_original: 'Source interface score', ppiflow_interface_score_matured: 'Refined interface score',
    ppiflow_interface_residue_count_original: 'Source interface residues',
    ppiflow_interface_residue_count_matured: 'Refined interface residues',
    ppiflow_anchor_count: 'PPIFlow anchor count', ppiflow_sample_index: 'PPIFlow sample',
    overall_plddt: 'Overall pLDDT', overall_ptm: 'Overall pTM', overall_pae: 'Overall PAE',
    plddt_coi: 'pLDDT (chain of interest)', ptm_coi: 'pTM (chain of interest)', pae_coi: 'PAE (chain of interest)',
    fraction_disordered: 'Fraction disordered', has_clash: 'Has clash', rmsd: 'RMSD',
};

export function metricLabel(key: string): string {
    return LABELS[key] ?? key.replace(/([a-z\d])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').replace(/^./, char => char.toUpperCase());
}

export function formatMetric(value: unknown): string {
    if (value === undefined) return 'Not reported';
    if (value === null) return 'Explicit null';
    if (isFiniteMetric(value)) return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
    if (typeof value === 'object') {
        try { return JSON.stringify(value) ?? String(value); } catch { return String(value); }
    }
    return String(value);
}
