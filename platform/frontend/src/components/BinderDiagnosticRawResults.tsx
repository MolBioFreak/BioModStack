import type { SelectedNativeResult } from '../lib/api';

type Row = Record<string, unknown>;
const object = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
const display = (value: unknown) => value === undefined || value === null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);

/** Preserve every observed metric/sample, without aggregation or interpretation. */
export function diagnosticRows(native: SelectedNativeResult): Row[] {
    return native.records.flatMap(record => {
        const candidate = record.design_id ?? record.candidate_id ?? record.candidate_key;
        const source = record.source_identity ?? object(native.source_identities)[String(candidate)];
        const identity = { candidate, sample: record.sample_id, state: object(source).target_state,
            artifact: object(source).artifact_id, source_job: native.source_job_id, root_job: native.lineage_root_job_id };
        const conditions = object(record.conditions);
        if (Object.keys(conditions).length) return Object.entries(conditions).flatMap(([condition, value]) => {
            const samples = object(value).samples;
            return Array.isArray(samples) ? samples.map(sample => ({ ...identity, condition, ...object(sample) })) : [];
        });
        return [{ ...identity, ...object(record.raw_metrics) }];
    });
}
const columns = (rows: Row[]) => [...new Set(rows.flatMap(row => Object.keys(row)))];
export function diagnosticCsv(native: SelectedNativeResult): string {
    const rows = diagnosticRows(native), fields = columns(rows);
    const cell = (value: unknown) => '"' + display(value).replace(/"/g, '""') + '"';
    return [fields.map(cell).join(','), ...rows.map(row => fields.map(key => cell(row[key])).join(','))].join('\r\n');
}

export default function BinderDiagnosticRawResults({ native, jobId }: { native: SelectedNativeResult; jobId: string }) {
    const rows = diagnosticRows(native), fields = columns(rows);
    return <div aria-label="Native selected records">
        <p>Raw model observations only. Sequence-only complex cofolding does not measure recovery of the supplied pose; supplied-geometry patch sampling does not establish affinity.</p>
        <div className="flex flex-wrap gap-4 my-2">
            <a download={`${jobId}-raw.csv`} href={`data:text/csv;charset=utf-8,${encodeURIComponent(diagnosticCsv(native))}`}>Export raw observations CSV</a>
            <a download={`${jobId}-receipt.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(native, null, 2))}`}>Export complete readback JSON</a>
        </div>
        <div className="overflow-x-auto"><table aria-label="Raw diagnostic observations" className="w-full text-xs">
            <thead><tr>{fields.map(key => <th className="p-2 text-left" key={key}>{key.replaceAll('_', ' ')}</th>)}</tr></thead>
            <tbody>{rows.map((row, index) => <tr key={index}>{fields.map(key => <td className="p-2 align-top" key={key}>{display(row[key])}</td>)}</tr>)}</tbody>
        </table></div>
        <details className="mt-2"><summary>Source lineage, settings and native receipt</summary><pre className="overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(native, null, 2)}</pre></details>
    </div>;
}
