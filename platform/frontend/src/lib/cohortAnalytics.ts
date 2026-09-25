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
    const total = fractions.reduce((sum, value) => sum + value, 0);
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
}

function sum(values: number[]): number {
    let total = 0, correction = 0;
    for (const value of values) {
        const adjusted = value - correction;
        const next = total + adjusted;
        correction = (next - total) - adjusted;
        total = next;
    }
    return total;
}

/** Center before scaling to retain small variation around large offsets. */
function normalized(values: number[]) {
    let anchor = values[0];
    let shifted = values.map(value => value - anchor);
    if (shifted.some(value => !Number.isFinite(value))) {
        anchor = 0;
        shifted = values;
    }
    const scale = shifted.reduce((largest, value) => Math.max(largest, Math.abs(value)), 0) || 1;
    const scaled = shifted.map(value => value / scale);
    const center = sum(scaled) / values.length;
    return { anchor, scale, center, deviations: scaled.map(value => value - center) };
}

/** Type-7 quantiles: linearly interpolate at (n - 1) * p. */
function quantile(sorted: number[], p: number): number {
    const index = (sorted.length - 1) * p;
    const low = Math.floor(index), fraction = index - low;
    if (!fraction) return sorted[low];
    return sorted[low] * (1 - fraction) + sorted[low + 1] * fraction;
}

export function summarizeMetric(rows: CohortRow[], key: string): MetricSummary {
    const result: MetricSummary = { observed: 0, missing: 0, nulls: 0, nonNumeric: 0,
        min: null, max: null, mean: null, median: null, q1: null, q3: null, stdDev: null };
    const values: number[] = [];
    for (const row of rows) {
        const value = Object.prototype.hasOwnProperty.call(row.values, key) ? row.values[key] : undefined;
        if (value === undefined) result.missing++;
        else if (value === null) result.nulls++;
        else if (isFiniteMetric(value)) values.push(value);
        else result.nonNumeric++;
    }
    result.observed = values.length;
    if (!values.length) return result;
    const sorted = [...values].sort((a, b) => a - b);
    const { anchor, scale, center, deviations } = normalized(values);
    const mean = anchor + center * scale;
    const sd = values.length > 1 ? Math.sqrt(sum(deviations.map(value => value * value)) / (values.length - 1)) * scale : null;
    return { ...result, min: sorted[0], max: sorted[sorted.length - 1],
        mean: Number.isFinite(mean) ? mean : null,
        median: quantile(sorted, 0.5), q1: quantile(sorted, 0.25), q3: quantile(sorted, 0.75),
        stdDev: sd !== null && Number.isFinite(sd) ? sd : null };
}

export interface CorrelationSummary {
    pairs: number;
    r: number | null;
    reason: 'insufficient pairs' | 'constant metric' | 'numerical range' | null;
}

/** Pairwise complete finite observations; no imputation, p-values, or verdict. */
export function correlateMetrics(rows: CohortRow[], xKey: string, yKey: string): CorrelationSummary {
    const xs: number[] = [], ys: number[] = [];
    for (const row of rows) {
        const x = row.values[xKey], y = row.values[yKey];
        if (isFiniteMetric(x) && isFiniteMetric(y)) { xs.push(x); ys.push(y); }
    }
    const pairs = xs.length;
    if (pairs < 3) return { pairs, r: null, reason: 'insufficient pairs' };
    const dx = normalized(xs).deviations, dy = normalized(ys).deviations;
    const xx = sum(dx.map(value => value * value)), yy = sum(dy.map(value => value * value));
    if (xx === 0 || yy === 0) return { pairs, r: null, reason: 'constant metric' };
    const r = sum(dx.map((value, index) => value * dy[index])) / Math.sqrt(xx) / Math.sqrt(yy);
    return Number.isFinite(r) ? { pairs, r: Math.max(-1, Math.min(1, r)), reason: null }
        : { pairs, r: null, reason: 'numerical range' };
}

const LABELS: Record<string, string> = {
    seq_length: 'Sequence length', dsasa: 'dSASA', num_ca_ca_clashes: 'CA–CA clashes',
    plddt: 'pLDDT', iptm: 'iPTM', ptm: 'pTM', pae: 'PAE',
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
