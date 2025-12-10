/**
 * JobDetailsPanel - Expandable panel showing job results with 3D visualization
 * 
 * For DiffDock jobs, fetches SDF files and displays them with MolViewer.
 */

import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { MolViewer } from './MolViewer';
import type { Job } from '../lib/api';

interface DockingResult {
    name: string;
    path: string;
    absolute_path: string;
    confidence: number | null;
    rank: number | null;
    complex_name: string;
}

interface StructureFile {
    name: string;
    filename: string;
    path: string;
    type: 'pdb' | 'cif';
    size_bytes: number;
}

interface JobDetailsPanelProps {
    job: Job;
    onClose: () => void;
}

export function JobDetailsPanel({ job, onClose }: JobDetailsPanelProps) {
    const [selectedPose, setSelectedPose] = useState<number>(0);
    const [pdbContent, setPdbContent] = useState<string>('');
    const [sdfContents, setSdfContents] = useState<Record<string, string>>({});

    // State for structure file viewer
    const [selectedStructure, setSelectedStructure] = useState<StructureFile | null>(null);
    const [structureContent, setStructureContent] = useState<string>('');

    // Check if this is a docking job
    const isDockingJob = job.model_id === 'diffdock' || job.mode?.includes('dock');

    // Fetch docking results
    const { data: dockingData, isLoading: dockingLoading } = useQuery({
        queryKey: ['docking-results', job.id],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${job.id}/docking-results`);
            if (!res.ok) throw new Error('Failed to fetch docking results');
            return res.json();
        },
        enabled: isDockingJob && job.status === 'completed',
    });

    // Fetch structure files for non-docking jobs
    const { data: structureData, isLoading: structureLoading } = useQuery<{ structures: StructureFile[], count: number }>({
        queryKey: ['structure-files', job.id],
        queryFn: async () => {
            const res = await fetch(`/api/jobs/${job.id}/structure-files`);
            if (!res.ok) throw new Error('Failed to fetch structure files');
            return res.json();
        },
        enabled: !isDockingJob && job.status === 'completed',
    });

    // Auto-select first structure when data loads
    useEffect(() => {
        if (structureData?.structures && structureData.structures.length > 0 && !selectedStructure) {
            setSelectedStructure(structureData.structures[0]);
        }
    }, [structureData, selectedStructure]);

    // Fetch structure content when selected structure changes
    useEffect(() => {
        if (selectedStructure) {
            fetch(`/api/files/pdb/${selectedStructure.path}`)
                .then(res => res.text())
                .then(content => setStructureContent(content))
                .catch(err => {
                    console.error('Failed to fetch structure:', err);
                    setStructureContent('');
                });
        } else {
            setStructureContent('');
        }
    }, [selectedStructure]);

    // Fetch SDF content for selected pose
    useEffect(() => {
        if (dockingData?.sdfs?.length > 0 && selectedPose < dockingData.sdfs.length) {
            const sdf = dockingData.sdfs[selectedPose];
            if (!sdfContents[sdf.name]) {
                fetch(`/api/jobs/${job.id}/docking-results/${sdf.name}`)
                    .then(res => res.text())
                    .then(content => {
                        setSdfContents(prev => ({ ...prev, [sdf.name]: content }));
                    })
                    .catch(console.error);
            }
        }
    }, [job.id, dockingData, selectedPose, sdfContents]);

    // Fetch PDB content from API
    useEffect(() => {
        if (isDockingJob && job.status === 'completed') {
            fetch(`/api/jobs/${job.id}/protein-pdb`)
                .then(res => {
                    if (res.ok) return res.text();
                    throw new Error('PDB not found');
                })
                .then(content => {
                    if (content && content.trim()) {
                        setPdbContent(content);
                    }
                })
                .catch(err => {
                    console.log('Could not load protein PDB:', err.message);
                    // PDB is optional - viewer will still show SDF
                });
        }
    }, [job.id, isDockingJob, job.status]);

    const poses = dockingData?.sdfs || [];
    const currentSdf = poses[selectedPose];
    const currentSdfContent = currentSdf ? sdfContents[currentSdf.name] : '';

    return (
        <tr>
            <td colSpan={6} className="bg-slate-900/50 border-b border-slate-700">
                <div className="p-6">
                    {/* Header */}
                    <div className="flex justify-between items-center mb-4">
                        <h3 className="text-lg font-semibold text-white">
                            {job.name} - Results
                        </h3>
                        <button
                            onClick={onClose}
                            className="text-slate-400 hover:text-white transition-colors"
                        >
                            ✕
                        </button>
                    </div>

                    {/* Job Info */}
                    <div className="grid grid-cols-3 gap-4 mb-6 text-sm">
                        <div>
                            <span className="text-slate-400">Model:</span>
                            <span className="text-white ml-2">{job.model_id}</span>
                        </div>
                        <div>
                            <span className="text-slate-400">Mode:</span>
                            <span className="text-white ml-2">{job.mode}</span>
                        </div>
                        <div>
                            <span className="text-slate-400">Output:</span>
                            <span className="text-slate-300 ml-2 text-xs">{job.output_dir}</span>
                        </div>
                    </div>

                    {/* Docking Results */}
                    {isDockingJob && job.status === 'completed' && (
                        <div>
                            {dockingLoading ? (
                                <div className="flex items-center justify-center py-8">
                                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500" />
                                </div>
                            ) : poses.length > 0 ? (
                                <div>
                                    {/* Pose Selector */}
                                    <div className="flex items-center gap-4 mb-4">
                                        <label className="text-slate-400 text-sm">Docking Pose:</label>
                                        <select
                                            value={selectedPose}
                                            onChange={(e) => setSelectedPose(Number(e.target.value))}
                                            className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-purple-500"
                                        >
                                            {poses.map((pose: DockingResult, idx: number) => (
                                                <option key={idx} value={idx}>
                                                    {pose.name} {pose.confidence !== null ? `(conf: ${pose.confidence.toFixed(2)})` : ''}
                                                </option>
                                            ))}
                                        </select>
                                        <span className="text-slate-500 text-sm">
                                            {poses.length} poses ranked by confidence (lower = better)
                                        </span>
                                    </div>

                                    {/* 3D Viewer */}
                                    <div className="bg-slate-800/50 rounded-xl overflow-hidden">
                                        {currentSdfContent ? (
                                            <MolViewer
                                                pdbContent={pdbContent || undefined}
                                                sdfContent={currentSdfContent}
                                                height={400}
                                                backgroundColor="#1e293b"
                                            />
                                        ) : (
                                            <div className="flex items-center justify-center h-[400px] text-slate-500">
                                                Loading 3D structure...
                                            </div>
                                        )}
                                    </div>

                                    {/* Confidence Score */}
                                    {currentSdf?.confidence !== null && (
                                        <div className="mt-4 text-center">
                                            <span className="text-slate-400">Confidence Score:</span>
                                            <span className={`ml-2 font-semibold ${currentSdf.confidence < -2 ? 'text-green-400' :
                                                currentSdf.confidence < 0 ? 'text-yellow-400' : 'text-red-400'
                                                }`}>
                                                {currentSdf.confidence.toFixed(2)}
                                            </span>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="text-center py-8 text-slate-500">
                                    No docking results found for this job
                                </div>
                            )}
                        </div>
                    )}

                    {/* Non-docking job info - Structure Files with 3D Viewer */}
                    {!isDockingJob && job.status === 'completed' && (
                        <div>
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="text-md font-medium text-white">
                                    Structure Files ({structureData?.count || 0})
                                </h3>
                            </div>

                            {structureLoading ? (
                                <div className="flex items-center justify-center py-4">
                                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-purple-500" />
                                </div>
                            ) : structureData?.structures && structureData.structures.length > 0 ? (
                                <div>
                                    {/* Structure Selector */}
                                    <div className="flex items-center gap-3 mb-3">
                                        <label className="text-sm text-slate-400">Select structure:</label>
                                        <select
                                            value={selectedStructure?.path || ''}
                                            onChange={(e) => {
                                                const struct = structureData.structures.find(s => s.path === e.target.value);
                                                setSelectedStructure(struct || null);
                                            }}
                                            className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                                        >
                                            {structureData.structures.map((struct) => (
                                                <option key={struct.path} value={struct.path}>
                                                    [{struct.type.toUpperCase()}] {struct.name} ({(struct.size_bytes / 1024).toFixed(1)} KB)
                                                </option>
                                            ))}
                                        </select>
                                        {selectedStructure && (
                                            <a
                                                href={`/api/files/download/${selectedStructure.path}`}
                                                download={selectedStructure.filename}
                                                className="px-3 py-2 bg-slate-700 text-slate-300 rounded-lg text-sm hover:bg-slate-600 transition-colors"
                                            >
                                                ↓ Download
                                            </a>
                                        )}
                                    </div>

                                    {/* 3D Viewer */}
                                    <div className="bg-slate-900/50 rounded-xl overflow-hidden">
                                        {structureContent ? (
                                            <MolViewer
                                                pdbContent={structureContent}
                                                structureFormat={selectedStructure?.type}
                                                height={350}
                                                backgroundColor="#0f172a"
                                            />
                                        ) : (
                                            <div className="flex items-center justify-center h-[350px] text-slate-500">
                                                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-purple-500 mr-3" />
                                                Loading structure...
                                            </div>
                                        )}
                                    </div>

                                    {/* Currently viewing info */}
                                    {selectedStructure && (
                                        <div className="mt-2 text-center text-sm">
                                            <span className={`px-2 py-0.5 rounded text-xs font-medium uppercase mr-2 ${selectedStructure.type === 'pdb'
                                                ? 'bg-green-500/20 text-green-400'
                                                : 'bg-blue-500/20 text-blue-400'
                                                }`}>
                                                {selectedStructure.type}
                                            </span>
                                            <span className="text-slate-300">{selectedStructure.name}</span>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="text-center py-4 text-slate-500">
                                    No structure files found
                                </div>
                            )}

                            <div className="mt-3 text-xs text-slate-500">
                                Directory: <code className="text-purple-400">{job.output_dir}</code>
                            </div>
                        </div>
                    )}

                    {/* Job not completed */}
                    {job.status !== 'completed' && (
                        <div className="text-center py-8 text-slate-500">
                            Job is {job.status}. Results will be available when completed.
                        </div>
                    )}
                </div>
            </td>
        </tr>
    );
}

export default JobDetailsPanel;
