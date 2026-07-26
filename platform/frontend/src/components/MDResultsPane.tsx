import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data, Layout, PlotMouseEvent } from 'plotly.js';

import {
    fetchMDAnalysis,
    fetchMDArtifacts,
    fetchMDSummary,
    retryMDAnalysis,
    type MDAnalysisPoint,
    type MDTrajectoryFrameMap,
} from '../lib/api';
import MolstarViewer from './MolstarViewer';
import type { MDSceneState } from '../structureViewer/contracts/mdTrajectory';

const layout: Partial<Layout> = {
    autosize: true,
    height: 390,
    margin: { l: 58, r: 24, t: 32, b: 52 },
    paper_bgcolor: '#0f172a',
    plot_bgcolor: '#0f172a',
    font: { color: '#cbd5e1' },
    xaxis: { title: { text: 'Time (ps)' }, gridcolor: '#334155' },
    yaxis: { title: { text: 'Backbone RMSD (Å)' }, gridcolor: '#334155' },
};

export default function MDResultsPane({ jobId }: { jobId: string }) {
    const queryClient = useQueryClient();
    const [selectedReplica, setSelectedReplica] = useState<number | null>(null);
    const [selectedPoint, setSelectedPoint] = useState<MDAnalysisPoint | null>(null);
    useEffect(() => {
        setSelectedReplica(null);
        setSelectedPoint(null);
    }, [jobId]);
    const summary = useQuery({ queryKey: ['md-summary', jobId], queryFn: () => fetchMDSummary(jobId) });
    const artifacts = useQuery({ queryKey: ['md-artifacts', jobId], queryFn: () => fetchMDArtifacts(jobId) });
    const analysis = useQuery({
        queryKey: ['md-analysis', jobId],
        queryFn: () => fetchMDAnalysis(jobId),
        refetchInterval: (query) => query.state.data?.data.status === 'completed' ? false : 5_000,
    });
    const retry = useMutation({
        mutationFn: () => retryMDAnalysis(jobId),
        onSuccess: async () => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ['md-summary', jobId] }),
                queryClient.invalidateQueries({ queryKey: ['md-artifacts', jobId] }),
                queryClient.invalidateQueries({ queryKey: ['md-analysis', jobId] }),
                queryClient.invalidateQueries({ queryKey: ['jobs'] }),
            ]);
        },
    });
    const finalStructures = (artifacts.data?.data.artifacts ?? []).filter((item) => item.semantic_role === 'representative_structure');
    const finalStructure = finalStructures.find((item) => item.replica === selectedReplica) ?? finalStructures[0];
    const playbackCapability = summary.data?.data.trajectory_playback;
    const activeReplica = selectedReplica ?? finalStructure?.replica ?? (playbackCapability?.supported ? playbackCapability.replicas[0]?.replica : null) ?? null;
    const frameMapArtifact = (artifacts.data?.data.artifacts ?? []).find((item) => (
        item.replica === activeReplica && item.semantic_role === 'trajectory_frame_map'
    ));
    const frameMap = useQuery({
        queryKey: ['md-trajectory-frame-map', jobId, frameMapArtifact?.id],
        enabled: Boolean(frameMapArtifact),
        queryFn: async (): Promise<MDTrajectoryFrameMap> => {
            const response = await fetch(frameMapArtifact!.content_url, { credentials: 'same-origin' });
            if (!response.ok) throw new Error(`MD trajectory frame map request failed with HTTP ${response.status}`);
            return response.json() as Promise<MDTrajectoryFrameMap>;
        },
    });
    const reports = analysis.data?.data.reports ?? [];
    const molecularDynamics = useMemo<MDSceneState | undefined>(() => {
        if (activeReplica == null || !summary.data || !playbackCapability) return undefined;
        const topology = (artifacts.data?.data.artifacts ?? []).find((item) => item.replica === activeReplica && item.semantic_role === 'analysis_topology');
        const trajectory = (artifacts.data?.data.artifacts ?? []).find((item) => item.replica === activeReplica && item.semantic_role === 'analysis_trajectory');
        if (!topology || !trajectory || trajectory.format !== 'xtc' || !topology.atom_order_identity || topology.atom_order_identity !== trajectory.atom_order_identity) return undefined;
        const selectedFrame = selectedPoint && selectedPoint.replica === activeReplica
            ? frameMap.data?.frames.find((frame) => frame.source_frame === selectedPoint.source_frame)
            : undefined;
        return {
            activeReplica,
            replicas: [{
                replica: activeReplica,
                topologyArtifactId: topology.id,
                trajectoryArtifactId: trajectory.id,
                atomOrderIdentity: topology.atom_order_identity,
                topologySha256: topology.sha256,
                trajectorySha256: trajectory.sha256,
                trajectoryFormat: 'xtc',
            }],
            playbackCapability,
            playback: {
                state: playbackCapability.supported ? 'stopped' : 'unsupported',
                selectedFrame: playbackCapability.supported && selectedFrame ? {
                    replica: activeReplica,
                    displayFrame: selectedFrame.display_frame,
                    sourceFrame: selectedFrame.source_frame,
                    timePs: selectedFrame.time_ps,
                    step: selectedFrame.step,
                } : undefined,
                framesPerSecond: 0,
            },
        };
    }, [activeReplica, artifacts.data?.data.artifacts, frameMap.data, playbackCapability, selectedPoint, summary.data]);
    const traces = useMemo<Data[]>(() => reports
        .filter((report) => report.status === 'completed' && report.replica != null && report.points?.length)
        .map((report) => ({
            type: 'scatter', mode: 'lines+markers', name: `Replica ${report.replica}`,
            x: report.points!.map((point) => point.time_ps),
            y: report.points!.map((point) => point.rmsd_angstrom),
            customdata: report.points!.map((point) => [point.replica, point.source_frame]),
            hovertemplate: 'Replica %{customdata[0]}<br>Time %{x:.2f} ps<br>Source frame %{customdata[1]}<br>RMSD %{y:.3f} Å<extra></extra>',
        })), [reports]);
    const replicaTrace = useMemo<Data[]>(() => [{
        type: 'bar',
        x: reports.filter((report) => report.status === 'completed').map((report) => `Replica ${report.replica}`),
        y: reports.filter((report) => report.status === 'completed').map((report) => report.summary?.final ?? null),
        customdata: reports.filter((report) => report.status === 'completed').map((report) => [report.replica ?? -1, report.inputs.trajectory_sha256 ?? '']),
        hovertemplate: 'Replica %{customdata[0]}<br>Final RMSD %{y:.3f} Å<br>Trajectory SHA-256 %{customdata[1]}<extra></extra>',
        marker: { color: '#22d3ee' },
    }], [reports]);
    const rgTraces = useMemo<Data[]>(() => reports
        .filter((report) => report.status === 'completed' && report.replica != null && report.points?.length)
        .map((report) => ({
            type: 'scatter', mode: 'lines', name: `Replica ${report.replica}`,
            x: report.points!.map((point) => point.time_ps),
            y: report.points!.map((point) => point.radius_of_gyration_angstrom),
            customdata: report.points!.map((point) => [point.replica, point.source_frame]),
            hovertemplate: 'Replica %{customdata[0]}<br>Time %{x:.2f} ps<br>Source frame %{customdata[1]}<br>Rg %{y:.3f} Å<extra></extra>',
        })), [reports]);
    const rmsfTraces = useMemo<Data[]>(() => reports
        .filter((report) => report.status === 'completed' && report.replica != null && report.residue_metrics?.length)
        .map((report) => ({
            type: 'scatter', mode: 'lines', name: `Replica ${report.replica}`,
            x: report.residue_metrics!.map((residue) => `${residue.segid}:${residue.resid}`),
            y: report.residue_metrics!.map((residue) => residue.backbone_rmsf_angstrom),
            customdata: report.residue_metrics!.map((residue) => [residue.resname, residue.backbone_atom_count]),
            hovertemplate: '%{x} %{customdata[0]}<br>Backbone RMSF %{y:.3f} Å<br>%{customdata[1]} backbone atoms<extra></extra>',
        })), [reports]);
    const loading = summary.isLoading || artifacts.isLoading || analysis.isLoading;
    if (loading) return <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-8 text-slate-300">Loading MD results…</div>;
    if (summary.isError || artifacts.isError || analysis.isError) {
        return <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-6 text-rose-200">MD result manifests are unavailable or failed validation.</div>;
    }
    const summaryData = summary.data!.data;
    const analysisData = analysis.data!.data;
    return (
        <section className="space-y-4" data-bms-result-pane="molecular-dynamics">
            <div className="grid gap-3 md:grid-cols-4">
                {[
                    ['Replicas', summaryData.replica_count], ['Artifacts', summaryData.artifact_count],
                    ['Dynamics', summaryData.status], ['Analysis', analysisData.status],
                ].map(([label, value]) => <div key={label} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4"><div className="text-xs uppercase text-slate-500">{label}</div><div className="mt-1 text-xl font-semibold text-white">{value}</div></div>)}
            </div>
            {analysisData.status !== 'completed' && <div className="flex items-center justify-between rounded-xl border border-amber-500/20 bg-amber-500/5 p-4"><div><div className="font-medium text-amber-100">Analysis requires attention</div><div className="mt-1 text-xs text-amber-200/70">{analysisData.retry.active ? 'A CPU-only analysis attempt is active.' : 'Retry schedules CPU analysis attempts only. Completed dynamics artifacts remain immutable.'}</div></div>{analysisData.retry.eligible && <button type="button" disabled={retry.isPending} onClick={() => retry.mutate()} className="rounded-lg border border-cyan-400/40 bg-cyan-500/10 px-4 py-2 text-sm font-medium text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50">{retry.isPending ? 'Scheduling…' : 'Retry analysis'}</button>}</div>}
            {retry.isError && <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">Analysis retry was rejected. Refresh the job state before retrying.</div>}
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-100">
                <div className="font-semibold">{analysisData.evidence.status.replace('_', ' ')}</div>
                <div className="mt-1 text-xs text-amber-200/80">{analysisData.evidence.reason} Frames are not treated as independent biological replicates.</div>
                <div className="mt-2 text-xs text-slate-300">Completed independent replicas: {analysisData.ensemble.completed_replicas} · mean replica RMSD: {analysisData.ensemble.mean_of_replica_mean_rmsd_angstrom?.toFixed(3) ?? 'n/a'} Å · sample SD across replica means: {analysisData.ensemble.sample_stdev_of_replica_mean_rmsd_angstrom?.toFixed(3) ?? 'n/a'} Å</div>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold text-white">Backbone RMSD</h2><span className="text-xs text-slate-400">Server-produced bounded points</span></div>
                    {analysisData.status === 'absent' ? <div className="py-20 text-center text-slate-400">Analysis has not been produced for this dynamics run.</div>
                        : traces.length === 0 ? <div className="py-20 text-center text-amber-300">Analysis is partial or failed. Inspect replica states below.</div>
                            : <Plot data={traces} layout={layout} config={{ responsive: true, displaylogo: false }} useResizeHandler className="w-full"
                                onClick={(event: PlotMouseEvent) => { const raw = event.points[0]?.customdata; if (Array.isArray(raw)) { const point = reports.flatMap((report) => report.points ?? []).find((candidate) => candidate.replica === Number(raw[0]) && candidate.source_frame === Number(raw[1])); if (point) { setSelectedPoint(point); setSelectedReplica(point.replica); } } }} />}
                    {selectedPoint && <div className="mt-2 rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-xs text-cyan-100">Replica {selectedPoint.replica} · source frame {selectedPoint.source_frame} · {selectedPoint.time_ps.toFixed(2)} ps · {selectedPoint.rmsd_angstrom.toFixed(3)} Å</div>}
                    <div className="mt-3 flex flex-wrap gap-2">{analysisData.replica_states.map((state) => <span key={state.replica} className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300">Replica {state.replica}: {state.status}</span>)}</div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold text-white">Replica final structure</h2>{finalStructures.length > 1 && <select value={finalStructure?.replica ?? ''} onChange={(event) => setSelectedReplica(Number(event.target.value))} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm">{finalStructures.map((item) => <option key={item.id} value={item.replica}>Replica {item.replica}</option>)}</select>}</div>
                    {(finalStructure || molecularDynamics) ? <MolstarViewer structureUrl={finalStructure?.content_url ?? ''} format={finalStructure ? (finalStructure.format === 'cif' || finalStructure.format === 'mmcif' ? 'cif' : 'pdb') : 'pdb'} height={390} label={finalStructure ? `MD replica ${finalStructure.replica} final structure` : 'MD GRO+XTC trajectory'} showMetricWorkbench={false} molecularDynamics={molecularDynamics} artifactJobId={jobId} /> : <div className="py-20 text-center text-slate-400">No checksum-bound PDB/mmCIF final structure or GRO+XTC trajectory is available.</div>}
                    {finalStructure && <div className="mt-3 space-y-1 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-xs text-slate-300"><div>Replica {finalStructure.replica} · source frame {finalStructure.source_frame ?? 'n/a'} · {finalStructure.time_ps != null ? `${finalStructure.time_ps.toFixed(2)} ps` : 'time unavailable'}</div><div className="break-all text-slate-500">Structure SHA-256 {finalStructure.sha256}</div><div className="break-all text-slate-500">Source trajectory SHA-256 {finalStructure.source_trajectory_sha256 ?? 'unavailable'}</div><div className="text-slate-500">Selection: {finalStructure.selection_method ?? 'completed production final coordinates'}</div></div>}
                    {!summaryData.trajectory_playback.supported && <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">Trajectory playback unavailable: {summaryData.trajectory_playback.reason}. Plot selections retain exact replica/time/source-frame provenance but do not move Mol*.</div>}
                </div>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <h2 className="mb-3 font-semibold text-white">Radius of gyration</h2>
                    {rgTraces.length ? <Plot data={rgTraces} layout={{ ...layout, yaxis: { title: { text: 'Backbone Rg (Å)' }, gridcolor: '#334155' } }} config={{ responsive: true, displaylogo: false }} useResizeHandler className="w-full" /> : <div className="py-16 text-center text-slate-400">No completed radius-of-gyration series.</div>}
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <h2 className="mb-3 font-semibold text-white">Residue backbone RMSF</h2>
                    {rmsfTraces.length ? <Plot data={rmsfTraces} layout={{ ...layout, xaxis: { title: { text: 'Residue' }, gridcolor: '#334155' }, yaxis: { title: { text: 'Backbone RMSF (Å)' }, gridcolor: '#334155' } }} config={{ responsive: true, displaylogo: false }} useResizeHandler className="w-full" /> : <div className="py-16 text-center text-slate-400">No completed residue RMSF table.</div>}
                </div>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <h2 className="mb-3 font-semibold text-white">Replica comparison</h2>
                    {reports.some((report) => report.status === 'completed') ? <Plot data={replicaTrace} layout={{ ...layout, xaxis: { title: { text: 'Replica' }, gridcolor: '#334155' }, yaxis: { title: { text: 'Final backbone RMSD (Å)' }, gridcolor: '#334155' } }} config={{ responsive: true, displaylogo: false }} useResizeHandler className="w-full" /> : <div className="py-16 text-center text-slate-400">No completed replica analysis is available.</div>}
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <h2 className="mb-3 font-semibold text-white">Replica QC provenance</h2>
                    <div className="space-y-2">{summaryData.replicas.map((replica) => <div key={replica.replica} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-sm"><div className="flex justify-between"><span className="font-medium text-white">Replica {replica.replica}</span><span className="text-slate-300">{replica.status}</span></div><div className="mt-1 text-xs text-slate-400">{replica.engine.name ?? 'unknown engine'} {replica.engine.version ?? ''} · {replica.engine.platform ?? 'unknown platform'}</div><div className="mt-1 text-xs text-slate-500">{Object.entries(replica.performance).length ? Object.entries(replica.performance).map(([key, value]) => `${key}: ${value}`).join(' · ') : 'No bounded engine performance metrics reported'}</div></div>)}</div>
                </div>
            </div>
        </section>
    );
}
