import { buildFileDownloadUrl, type Design, type Job, type StructureSourceSelection } from './api';
import type { CohortRow } from './cohortAnalytics';

export const isShapeResultJob = (job: Job | undefined) => job?.mode === 'shape_blueprint';
export const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Presentation paths preserve the producer's stage boundaries; never merge predictor scores. */
export function shapeMetricValues(design: Design): Record<string, unknown> {
    const metrics = record(design.confidence_metrics);
    const result: Record<string, unknown> = {};
    if (design.provenance && Object.hasOwn(design.provenance, 'sequence_engine')) result['sequence.designer'] = design.provenance.sequence_engine;
    const flatten = (value: unknown, prefix: string) => {
        const preferred = ['shape_total', 'shape_outside', 'sdf_positive_inside_fraction', 'plddt_overall', 'ca_rmsd_angstrom'];
        const entries = Object.entries(record(value));
        entries.sort(([a], [b]) => (preferred.includes(a) ? preferred.indexOf(a) : preferred.length) - (preferred.includes(b) ? preferred.indexOf(b) : preferred.length));
        for (const [key, item] of entries) {
            const path = `${prefix}.${key}`;
            if (item !== null && typeof item === 'object' && !Array.isArray(item)) flatten(item, path);
            else if (!Array.isArray(item)) result[path] = item;
        }
    };
    // These values belong to the primary document, not necessarily the RFD3 backbone.
    const primary = { ...metrics };
    delete primary.post_refold; delete primary.validator_evidence; delete primary.shape_metrics;
    flatten(primary, `primary (${String(design.provenance?.predictor ?? design.provenance?.sequence_policy ?? 'native')})`);
    flatten(metrics.shape_metrics, 'backbone admission');
    flatten(metrics.post_refold, 'post-refold');
    const evidence = record(metrics.validator_evidence);
    const records = evidence.records;
    if (Array.isArray(records)) records.forEach((entry, index) => {
        const row = record(entry);
        // Index is a displayed record index only, never a scientific sample join.
        flatten(row, `prediction ${String(row.validator ?? row.model_id ?? 'native')} [${String(row.sample_id ?? row.sample_index ?? `record ${index + 1}`)}]`);
    });
    else flatten(records, 'prediction');
    return result;
}
export const shapeCohort = (designs: Design[]): CohortRow[] => designs.map(design => ({ id: design.id, label: design.name, values: shapeMetricValues(design) }));

export interface ShapeDocument {
    key: string; label: string; format: 'pdb' | 'cif'; url: string;
    path?: string; sha256?: string; artifactId?: string; targetState?: string;
    modelNumber?: number; source: StructureSourceSelection; descriptor: Record<string, unknown>;
}
export function shapeDocuments(design: Design): ShapeDocument[] {
    return Object.entries(design.review_artifact_manifest?.artifacts ?? {}).flatMap(([key, value]) => {
        const doc = record(value);
        const path = typeof doc.path === 'string' ? doc.path : undefined;
        const format = doc.format === 'pdb' ? 'pdb' : doc.format === 'cif' || doc.format === 'mmcif' ? 'cif'
            : /\.(?:mmcif|cif)(?:\.gz)?$/i.test(path ?? '') ? 'cif' : /\.pdb$/i.test(path ?? '') ? 'pdb' : null;
        const url = typeof doc.download_url === 'string' ? doc.download_url : path ? buildFileDownloadUrl(path) : '';
        if (!format || !url) return [];
        const artifactId = typeof doc.artifact_id === 'string' ? doc.artifact_id : undefined;
        const targetState = typeof doc.target_state === 'string' ? doc.target_state : undefined;
        const sha256 = typeof doc.sha256 === 'string' ? doc.sha256 : undefined;
        const modelNumber = typeof doc.model_number === 'number' ? doc.model_number : undefined;
        const source: StructureSourceSelection = Object.keys(record(doc.source_structure)).length ? doc.source_structure as StructureSourceSelection
            : artifactId ? { job_id: design.job_id, design_id: design.id, document: { artifact_id: artifactId, ...(targetState === undefined ? {} : { target_state: targetState }) } }
            : key === 'structure' ? { job_id: design.job_id, design_id: design.id } : { path };
        return [{ key, label: [key, doc.validator, doc.sample_id].filter(value => value !== undefined).join(' · '), format, url, path, sha256, artifactId, targetState, modelNumber,
            source: { ...source, ...(sha256 ? { expected_sha256: sha256 } : {}), ...(modelNumber === undefined ? {} : { model_number: modelNumber }), output_format: 'native' }, descriptor: doc }];
    });
}

export type ShapeMetricFilter = { key: string; kind: 'range' | 'missing' | 'null' | 'nonNumeric'; min: string; max: string };
export function filterShapeCohort(rows: CohortRow[], search: string, filter: ShapeMetricFilter, selected?: Set<string>): CohortRow[] {
    const needle = search.trim().toLowerCase();
    return rows.filter(row => {
        if (selected && !selected.has(row.id)) return false;
        if (needle && !`${row.id} ${row.label} ${Object.values(row.values).join(' ')}`.toLowerCase().includes(needle)) return false;
        if (!filter.key) return true;
        const value = row.values[filter.key];
        if (filter.kind === 'missing') return value === undefined;
        if (filter.kind === 'null') return value === null;
        if (filter.kind === 'nonNumeric') return value != null && !(typeof value === 'number' && Number.isFinite(value));
        if (!filter.min.trim() && !filter.max.trim()) return true;
        return typeof value === 'number' && Number.isFinite(value) && (!filter.min.trim() || value >= Number(filter.min)) && (!filter.max.trim() || value <= Number(filter.max));
    });
}
export function shapeCsv(rows: CohortRow[], keys: string[]): string {
    const cell = (value: unknown) => {
        const text = value === undefined ? '' : value === null ? 'null' : String(value);
        return `"${(typeof value === 'string' && /^[=+\-@\t\r]/.test(text) ? `'${text}` : text).replaceAll('"', '""')}"`;
    };
    return [['design_id', 'candidate', ...keys], ...rows.map(row => [row.id, row.label, ...keys.map(key => row.values[key])])].map(row => row.map(cell).join(',')).join('\r\n');
}
