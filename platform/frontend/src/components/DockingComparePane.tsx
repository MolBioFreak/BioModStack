/**
 * DockingComparePane - Side-by-side comparison of DiffDock and Uni-Dock poses
 * 
 * Used in dual docking mode to visualize and compare poses from both engines.
 * Shows RMSD, agreement status, and consensus scoring.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import MolstarViewer from './MolstarViewer';

interface ComparisonData {
    diffdock_file: string;
    diffdock_confidence: number | null;
    diffdock_rank: number | null;
    unidock_file: string | null;
    unidock_affinity: number | null;
    unidock_rank: number | null;
    rmsd: number | null;
    agreement: boolean;
    consensus_score?: number;
}

interface ComparisonResult {
    comparisons: ComparisonData[];
    consensus_poses: ComparisonData[];
    summary: {
        total_diffdock_poses: number;
        total_unidock_poses: number;
        total_agreements: number;
        agreement_rate: number;
        rmsd_threshold: number;
    };
}

interface Props {
    jobId: string;
}

export function DockingComparePane({ jobId }: Props) {
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [viewMode, setViewMode] = useState<'all' | 'consensus'>('all');

    // Fetch comparison data
    const { data: comparison, isLoading, error } = useQuery<{ comparison: ComparisonResult }>({
        queryKey: ['docking-comparison', jobId],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${jobId}/docking-comparison`);
            if (!res.ok) {
                if (res.status === 404) {
                    return { comparison: null };
                }
                throw new Error('Failed to fetch docking comparison');
            }
            return res.json();
        },
    });

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500" />
            </div>
        );
    }

    if (error || !comparison?.comparison) {
        return (
            <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-6 text-center">
                <p className="text-amber-400">
                    Comparison data not available. This may not be a dual docking job.
                </p>
            </div>
        );
    }

    const { summary, comparisons, consensus_poses } = comparison.comparison;
    const poses = viewMode === 'consensus' ? consensus_poses : comparisons;
    const current = poses[selectedIndex];

    return (
        <div className="space-y-6">
            {/* Summary Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard
                    label="DiffDock Poses"
                    value={summary.total_diffdock_poses}
                    color="text-purple-400"
                />
                <StatCard
                    label="Uni-Dock Poses"
                    value={summary.total_unidock_poses}
                    color="text-emerald-400"
                />
                <StatCard
                    label="Agreements"
                    value={summary.total_agreements}
                    color="text-green-400"
                />
                <StatCard
                    label="Agreement Rate"
                    value={`${(summary.agreement_rate * 100).toFixed(0)}%`}
                    color="text-blue-400"
                />
            </div>

            {/* View Mode Toggle */}
            <div className="flex gap-2">
                <button
                    onClick={() => { setViewMode('all'); setSelectedIndex(0); }}
                    className={`px-4 py-2 rounded-lg font-medium transition-colors ${viewMode === 'all'
                            ? 'bg-purple-600 text-white'
                            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                >
                    All Comparisons ({comparisons.length})
                </button>
                <button
                    onClick={() => { setViewMode('consensus'); setSelectedIndex(0); }}
                    className={`px-4 py-2 rounded-lg font-medium transition-colors ${viewMode === 'consensus'
                            ? 'bg-green-600 text-white'
                            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                >
                    Consensus Only ({consensus_poses.length})
                </button>
            </div>

            {poses.length === 0 ? (
                <div className="text-center py-8 text-slate-500">
                    {viewMode === 'consensus'
                        ? 'No consensus poses found (no agreements below RMSD threshold)'
                        : 'No comparison data available'}
                </div>
            ) : (
                <>
                    {/* Pose Selector */}
                    <select
                        value={selectedIndex}
                        onChange={(e) => setSelectedIndex(Number(e.target.value))}
                        className="w-full bg-slate-800 border border-slate-600 rounded-lg px-4 py-3 text-white"
                    >
                        {poses.map((pose, idx) => (
                            <option key={idx} value={idx}>
                                Pose {idx + 1} - {pose.agreement ? '✅ Agreement' : '⚠️ Divergent'}
                                {pose.rmsd !== null ? ` (RMSD: ${pose.rmsd.toFixed(2)}Å)` : ''}
                                {pose.consensus_score !== undefined ? ` [Score: ${pose.consensus_score.toFixed(2)}]` : ''}
                            </option>
                        ))}
                    </select>

                    {/* Side-by-Side Viewers */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {/* DiffDock Panel */}
                        <div className="bg-purple-950/30 border border-purple-500/30 rounded-xl p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-lg font-semibold text-purple-400 flex items-center gap-2">
                                    <span className="w-2 h-2 bg-purple-500 rounded-full"></span>
                                    DiffDock (ML)
                                </h3>
                                {current?.diffdock_confidence !== null && (
                                    <span className="text-sm bg-purple-500/20 px-2 py-1 rounded text-purple-300">
                                        Confidence: {current.diffdock_confidence?.toFixed(2)}
                                    </span>
                                )}
                            </div>
                            {current?.diffdock_file ? (
                                <MolstarViewer
                                    structureUrl={`/api/jobs/${jobId}/docking-results/${current.diffdock_file}`}
                                    format="pdb"
                                    height={350}
                                    backgroundColor="#1e1b4b"
                                    alphafoldView={false}
                                />
                            ) : (
                                <div className="h-[350px] flex items-center justify-center text-slate-500 bg-slate-900/50 rounded-lg">
                                    No DiffDock pose
                                </div>
                            )}
                        </div>

                        {/* Uni-Dock Panel */}
                        <div className="bg-emerald-950/30 border border-emerald-500/30 rounded-xl p-4">
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-lg font-semibold text-emerald-400 flex items-center gap-2">
                                    <span className="w-2 h-2 bg-emerald-500 rounded-full"></span>
                                    Uni-Dock (Physics)
                                </h3>
                                {current?.unidock_affinity !== null && (
                                    <span className="text-sm bg-emerald-500/20 px-2 py-1 rounded text-emerald-300">
                                        Affinity: {current.unidock_affinity?.toFixed(1)} kcal/mol
                                    </span>
                                )}
                            </div>
                            {current?.unidock_file ? (
                                <MolstarViewer
                                    structureUrl={`/api/jobs/${jobId}/docking-results/${current.unidock_file}`}
                                    format="pdb"
                                    height={350}
                                    backgroundColor="#022c22"
                                    alphafoldView={false}
                                />
                            ) : (
                                <div className="h-[350px] flex items-center justify-center text-slate-500 bg-slate-900/50 rounded-lg">
                                    No matching Uni-Dock pose
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Comparison Metrics */}
                    {current && (
                        <div className="bg-slate-800/30 rounded-xl p-6 border border-slate-700">
                            <div className="grid grid-cols-3 gap-6 text-center">
                                <div>
                                    <span className="text-slate-400 text-sm block mb-1">RMSD</span>
                                    <div className={`text-3xl font-bold ${current.rmsd === null ? 'text-slate-500' :
                                            current.rmsd <= 2 ? 'text-green-400' :
                                                current.rmsd <= 4 ? 'text-yellow-400' : 'text-red-400'
                                        }`}>
                                        {current.rmsd !== null ? `${current.rmsd.toFixed(2)} Å` : '—'}
                                    </div>
                                    <span className="text-xs text-slate-500">
                                        Threshold: {summary.rmsd_threshold} Å
                                    </span>
                                </div>
                                <div>
                                    <span className="text-slate-400 text-sm block mb-1">Agreement</span>
                                    <div className="text-3xl font-bold">
                                        {current.agreement ? (
                                            <span className="text-green-400">✅ Yes</span>
                                        ) : (
                                            <span className="text-amber-400">⚠️ No</span>
                                        )}
                                    </div>
                                    <span className="text-xs text-slate-500">
                                        {current.agreement ? 'High confidence' : 'Investigate further'}
                                    </span>
                                </div>
                                <div>
                                    <span className="text-slate-400 text-sm block mb-1">Consensus Score</span>
                                    <div className="text-3xl font-bold text-blue-400">
                                        {current.consensus_score !== undefined
                                            ? current.consensus_score.toFixed(2)
                                            : '—'}
                                    </div>
                                    <span className="text-xs text-slate-500">
                                        Combined ranking
                                    </span>
                                </div>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

function StatCard({ label, value, color = 'text-white' }: {
    label: string;
    value: string | number;
    color?: string;
}) {
    return (
        <div className="bg-slate-800/50 rounded-xl p-4 text-center border border-slate-700/50">
            <div className="text-slate-400 text-sm mb-1">{label}</div>
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
        </div>
    );
}

export default DockingComparePane;
