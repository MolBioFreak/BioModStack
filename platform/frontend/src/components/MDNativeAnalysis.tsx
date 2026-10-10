import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';
import type { MDAnalysisReplicaReport, MDAnalysisReportSet } from '../lib/api';

// Native results and lane labels share the authoritative MD endpoint types.
export type MDLane = Pick<MDAnalysisReplicaReport, 'replica' | 'window_id' | 'replicate_index'>;
export const mdLaneLabel = (lane: MDLane) => lane.window_id != null
    ? `Window ${lane.window_id} · replicate ${lane.replicate_index ?? 'unavailable'} · lane ${lane.replica ?? 'unknown'}`
    : `Replica ${lane.replica ?? 'unknown'}`;
export type NativeReport = MDAnalysisReplicaReport;
export type NativeAnalysis = MDAnalysisReportSet;
const chartLayout: Partial<Layout> = { height: 320, autosize: true, margin: { l: 65, r: 20, t: 20, b: 65 }, paper_bgcolor: '#0f172a', plot_bgcolor: '#0f172a', font: { color: '#cbd5e1' } };
function Chart({ title, x, y, data }: { title: string; x: string; y: string; data: Data[] }) {
    return <div className="min-w-0"><h3 className="font-medium text-slate-100">{title}</h3>{data.some(trace => 'x' in trace && Array.isArray(trace.x) && trace.x.length) ? <Plot data={data} layout={{ ...chartLayout, xaxis: { title: { text: x } }, yaxis: { title: { text: y } } }} config={{ responsive: true, displaylogo: false }} useResizeHandler className="w-full" /> : <p>No samples published.</p>}</div>;
}
export default function MDNativeAnalysis({ analysis }: { analysis?: NativeAnalysis }) {
    if (!analysis) return null;
    const wham = analysis.wham;
    return <section aria-label="Native and selected-group analysis" className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-sm text-slate-300">
        <h2 className="font-semibold text-white">Optional analysis · {analysis.status}</h2>
        <p>Analysis availability does not change dynamics status. Missing measurements are not zero.</p>
        {analysis.collection && <p>Collection: {analysis.collection.status} · completed {analysis.collection.completed_analysis_children} · failed {analysis.collection.failed_analysis_children} · cancelled {analysis.collection.cancelled_analysis_children}</p>}
        {analysis.collection?.collection_errors?.map((error, index) => <p role="alert" key={index}>Collection: {error.code} · {error.message}</p>)}
        {analysis.execution?.map(child => <p key={child.job_id}>{mdLaneLabel({ replica: child.replica })} analysis execution: {child.status}{child.error && ` · ${typeof child.error === 'string' ? child.error : child.error.message}`}</p>)}
        {analysis.reports.map((report, index) => <div key={index} className="space-y-3 border-t border-slate-700 pt-3">
            <h3>{mdLaneLabel(report)} · structural analysis: {report.status}</h3>
            <p>{report.selection === null ? 'Structural selection disabled.' : report.selection ? `Selection: ${report.selection}` : ''} {report.reason} {report.failure?.message}</p>
            {report.specialized_analyzers?.map(contact => <details key={contact.analyzer_id} open={Boolean(contact.points?.length)}>
                <summary>{contact.analyzer_id} · {contact.status}</summary><p>{contact.reason} {contact.definition}</p>
                {contact.selection_a != null && <p>{contact.selection_a} ↔ {contact.selection_b} · cutoff {contact.cutoff_angstrom ?? 'unavailable'} Å</p>}
                {contact.points?.length ? <div className="grid gap-4 xl:grid-cols-2">
                    <Chart title="Selected-atom contacts" x="Time (ps)" y="Atom-pair count" data={[{ type: 'scatter', mode: 'lines', name: contact.analyzer_id, x: contact.points.map(p => p.time_ps), y: contact.points.map(p => p.contact_count) }]} />
                    <Chart title="Minimum pair distance" x="Time (ps)" y="Distance (Å)" data={[{ type: 'scatter', mode: 'lines', name: contact.analyzer_id, x: contact.points.map(p => p.time_ps), y: contact.points.map(p => p.minimum_distance_angstrom) }]} />
                </div> : null}
            </details>)}
            {report.pull_error && <p role="alert">Native pull analysis failed: {report.pull_error.message}</p>}
            {report.pull_coordinates?.map(pull => <div key={`${pull.coordinate}:${pull.column}`}>
                <h3>{pull.label} · coordinate {pull.coordinate} · XVG column {pull.column} · {mdLaneLabel({ ...report, replica: pull.replica, window_id: report.window_id ?? pull.window })}</h3>
                <p>Groups: {pull.groups.join(' ↔ ')} · {pull.points.length} displayed points of {pull.sample_count ?? 'unavailable'} native samples.</p>
                <div className="grid gap-4 xl:grid-cols-2">
                    <Chart title="Native pull trace" x={`Time (${pull.time_unit})`} y={`${pull.label} (${pull.unit})`} data={[{ type: 'scatter', mode: 'lines', name: pull.label, x: pull.points.map(p => p.time_ps), y: pull.points.map(p => p.value) }]} />
                    <Chart title={`Full-sample histogram · ${pull.histogram.sample_count} samples`} x={`${pull.label} (${pull.unit})`} y={`Samples (${pull.histogram.normalization})`} data={[{ type: 'bar', name: pull.label, x: pull.histogram.counts.map((_, i) => (pull.histogram.edges[i] + pull.histogram.edges[i + 1]) / 2), width: pull.histogram.counts.map((_, i) => pull.histogram.edges[i + 1] - pull.histogram.edges[i]), y: pull.histogram.counts, customdata: pull.histogram.counts.map((_, i) => [pull.histogram.edges[i], pull.histogram.edges[i + 1]]), hovertemplate: '[%{customdata[0]}, %{customdata[1]}]<br>%{y} samples<extra></extra>' }]} />
                </div>
            </div>)}
        </div>)}
        <div className="border-t border-slate-700 pt-3"><h3>Native 1D WHAM</h3>
            {!wham ? <p>No WHAM result published.</p> : <><p>{wham.method} · {wham.status} · Selected coordinate across windows; not a joint 2D PMF.</p>
                <p>{wham.request.windows.map(w => `Window ${w.window} · lane ${w.replica} · coordinate ${w.coordinate}/${w.coordinate_count}`).join('; ')}</p>
                {wham.error && <p role="alert">{wham.error.message}{wham.returncode != null && ` · exit ${wham.returncode}`}</p>}
                {wham.status === 'completed' && <div className="grid gap-4 xl:grid-cols-2">
                    <Chart title="Native potential of mean force" x={`Coordinate (${wham.coordinate_unit})`} y={`PMF (${wham.energy_unit})`} data={[{ type: 'scatter', mode: 'lines', name: '1D PMF', x: wham.points?.map(p => p.coordinate) ?? [], y: wham.points?.map(p => p.pmf_kj_mol) ?? [] }]} />
                    <Chart title="Native window histograms" x={`Coordinate (${wham.coordinate_unit})`} y="Native histogram count" data={wham.histograms?.columns.map((window, i) => ({ type: 'scatter', mode: 'lines', name: `Window ${window.window} · lane ${window.replica} · coordinate ${window.coordinate}`, x: wham.histograms!.rows.map(row => row[0]), y: wham.histograms!.rows.map(row => row[i + 1] ?? null) })) ?? []} />
                </div>}
            </>}
        </div>
    </section>;
}
