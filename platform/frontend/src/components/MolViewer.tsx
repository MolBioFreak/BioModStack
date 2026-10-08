/**
 * MolViewer - 3D Molecular Structure Viewer using 3Dmol.js
 * 
 * Features:
 * - Protein visualization (Cartoon/Surface)
 * - Ligand binding visualization (Stick/Sphere)
 * - Color by pLDDT (B-factor spectrum) or rainbow
 * - Auto-spin toggle
 * - Image export
 */

import { useEffect, useRef, useState, useImperativeHandle, forwardRef } from 'react';

// 3Dmol is loaded via script tag
declare global {
    interface Window {
        $3Dmol: any;
    }
}

export interface MolViewerProps {
    /** Main PDB structure content */
    pdbContent?: string;
    /** Secondary PDB content for overlay comparison */
    overlayPdbContent?: string;
    /** Structure format (pdb/cif) */
    structureFormat?: 'pdb' | 'cif';
    /** Optional ligand SDF content */
    sdfContent?: string;
    /** Height in pixels */
    height?: number;
    /** Background color */
    backgroundColor?: string;
    /** Show surface representation */
    showSurface?: boolean;
    /** Color scheme for protein */
    colorScheme?: 'rainbow' | 'plddt' | 'sse';
    /** Representation style */
    representationStyle?: 'cartoon' | 'sticks' | 'sphere' | 'ball-stick';
}

export interface MolViewerRef {
    exportImage: () => string; // Returns data URL
}

export const MolViewer = forwardRef<MolViewerRef, MolViewerProps>(({
    pdbContent,
    overlayPdbContent,
    structureFormat,
    sdfContent,
    height = 500,
    backgroundColor = '#0f172a',
    showSurface = false,
    colorScheme = 'plddt',
    representationStyle = 'cartoon'
}, ref) => {
    const viewerRef = useRef<HTMLDivElement>(null);
    const viewerInstance = useRef<any>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [isSpinning, setIsSpinning] = useState(false);

    // Expose methods to parent
    useImperativeHandle(ref, () => ({
        exportImage: () => {
            if (viewerInstance.current) {
                return viewerInstance.current.pngURI();
            }
            return '';
        }
    }));

    useEffect(() => {
        setError(null);
        setLoading(true);

        // Load 3Dmol.js if missing
        if (!window.$3Dmol) {
            const script = document.createElement('script');
            script.src = 'https://3dmol.org/build/3Dmol-min.js';
            script.async = true;
            script.onload = () => initViewer();
            script.onerror = () => {
                setError('Failed to load 3Dmol.js library');
                setLoading(false);
            };
            document.head.appendChild(script);
        } else {
            initViewer();
        }

        function initViewer() {
            if (!viewerRef.current || !window.$3Dmol) return;

            try {
                // Cleanup old viewer
                if (viewerInstance.current) {
                    viewerInstance.current.clear();
                    viewerInstance.current = null;
                }

                // Initialize
                const viewer = window.$3Dmol.createViewer(viewerRef.current, {
                    backgroundColor,
                    antialias: true, // smoother edges
                });
                viewerInstance.current = viewer;

                // Helper to add protein
                const addProtein = (content: string, isOverlay = false) => {
                    let format = structureFormat || 'pdb';
                    if (!structureFormat && (content.includes('data_') || content.includes('_atom_site.'))) {
                        format = 'cif';
                    }

                    viewer.addModel(content, format);
                    const model = viewer.getModel(isOverlay ? -1 : 0); // -1 is last added


                    if (colorScheme === 'plddt') {
                        // pLDDT coloring: Fuchsia (high) → Red (low)
                        // Auto-detect 0-1 vs 0-100 B-factor scale (CIF uses 0-1)
                        model.setStyle({}, { cartoon: {} });
                        model.setColorByFunction({}, (atom: any) => {
                            let b = atom.b;
                            if (b <= 1) b = b * 100; // Scale 0-1 to 0-100
                            if (b >= 90) return '#d946ef'; // Very High (Fuchsia)
                            if (b >= 70) return '#f472b6'; // High (Pink)
                            if (b >= 50) return '#fb923c'; // Low (Orange)
                            return '#ef4444'; // Very Low (Red)
                        });
                    } else if (colorScheme === 'sse') {
                        // Secondary Structure Element coloring
                        model.setStyle({}, { cartoon: { colorscheme: 'ss' } });
                    } else {
                        // Rainbow
                        model.setStyle({}, { cartoon: { color: 'spectrum' } });
                    }

                    // Apply non-cartoon representation styles (only if not cartoon mode)
                    if (representationStyle !== 'cartoon') {
                        if (representationStyle === 'sticks') {
                            model.setStyle({}, { stick: { radius: 0.15 } });
                        } else if (representationStyle === 'sphere') {
                            model.setStyle({}, { sphere: { scale: 0.25 } });
                        } else if (representationStyle === 'ball-stick') {
                            model.setStyle({}, {
                                stick: { radius: 0.1 },
                                sphere: { scale: 0.18 }
                            });
                        }
                    }

                    // Ghost opacity for overlay
                    if (isOverlay) {
                        model.setStyle({}, { cartoon: { color: 'white', opacity: 0.5 } });
                    }

                    // Surface
                    if (showSurface && !isOverlay) {
                        viewer.addSurface(window.$3Dmol.SurfaceType.VDW, {
                            opacity: 0.3,
                            color: 'white'
                        }, { model: model });
                    }
                };

                // Add Main Model
                if (pdbContent) addProtein(pdbContent, false);

                // Add Overlay Model
                if (overlayPdbContent) addProtein(overlayPdbContent, true);

                // Add Ligand
                if (sdfContent) {
                    viewer.addModel(sdfContent, 'sdf');
                    viewer.setStyle({ model: -1 }, {
                        stick: { radius: 0.2, colorscheme: 'greenCarbon' }
                    });
                }

                viewer.zoomTo();
                viewer.render();

                // Hover labels for amino acid info
                let hoverLabel: any = null;
                viewer.setHoverable({}, true,
                    function (atom: any) {
                        if (atom && !hoverLabel) {
                            const aaMap: { [key: string]: string } = {
                                'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
                                'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
                                'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
                                'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
                            };
                            const oneLetter = aaMap[atom.resn] || '?';
                            hoverLabel = viewer.addLabel(`${atom.resn} (${oneLetter}) #${atom.resi}`, {
                                position: { x: atom.x, y: atom.y, z: atom.z },
                                backgroundColor: 'rgba(15, 23, 42, 0.9)',
                                fontColor: '#f8fafc',
                                fontSize: 11,
                                showBackground: true
                            });
                            viewer.render();
                        }
                    },
                    function () {
                        if (hoverLabel) {
                            viewer.removeLabel(hoverLabel);
                            hoverLabel = null;
                            viewer.render();
                        }
                    }
                );

                // Click handler to highlight residue
                viewer.setClickable({}, true, function (atom: any, v: any) {
                    if (atom) {
                        v.addStyle({ resi: atom.resi, chain: atom.chain }, {
                            stick: { radius: 0.2, color: '#22d3ee' }
                        });
                        v.render();
                    }
                });

                setLoading(false);

            } catch (e) {
                console.error('MolViewer render error:', e);
                setError('Failed to render structure');
                setLoading(false);
            }
        }
    }, [pdbContent, overlayPdbContent, sdfContent, backgroundColor, showSurface, colorScheme, structureFormat, representationStyle]);

    // Spin effect
    useEffect(() => {
        if (viewerInstance.current) {
            viewerInstance.current.spin(isSpinning);
        }
    }, [isSpinning]);

    return (
        <div className="relative group">
            {error && (
                <div className="absolute inset-0 flex items-center justify-center bg-slate-900 text-red-400 z-20">
                    ⚠️ {error}
                </div>
            )}

            {loading && (
                <div className="absolute inset-0 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm z-10 text-slate-300">
                    <div className="flex flex-col items-center gap-2">
                        <div className="w-8 h-8 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
                        <span className="text-xs font-medium uppercase tracking-wider">Rendering 3D...</span>
                    </div>
                </div>
            )}

            <div
                ref={viewerRef}
                style={{ width: '100%', height: `${height}px` }}
                className="w-full rounded-xl overflow-hidden shadow-inner bg-slate-900"
            />

            {/* Floating Controls */}
            {!loading && !error && (
                <div className="absolute bottom-4 right-4 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                    <button
                        onClick={() => setIsSpinning(!isSpinning)}
                        className={`px-3 py-1.5 rounded-lg backdrop-blur-md border transition-colors text-xs font-medium ${isSpinning
                            ? 'bg-blue-500/80 text-white border-blue-400'
                            : 'bg-slate-800/80 text-slate-300 border-slate-700 hover:bg-slate-700'
                            }`}
                        title="Toggle Spin"
                    >
                        {isSpinning ? 'Stop Spin' : 'Spin'}
                    </button>
                    <button
                        onClick={() => {
                            if (viewerInstance.current) {
                                viewerInstance.current.zoomTo();
                            }
                        }}
                        className="px-3 py-1.5 bg-slate-800/80 text-slate-300 rounded-lg backdrop-blur-md border border-slate-700 hover:bg-slate-700 text-xs font-medium"
                        title="Reset View"
                    >
                        Reset
                    </button>
                    <button
                        onClick={() => {
                            if (viewerInstance.current) {
                                const img = viewerInstance.current.pngURI();
                                const link = document.createElement('a');
                                link.href = img;
                                link.download = 'structure.png';
                                link.click();
                            }
                        }}
                        className="px-3 py-1.5 bg-slate-800/80 text-slate-300 rounded-lg backdrop-blur-md border border-slate-700 hover:bg-slate-700 text-xs font-medium"
                        title="Save PNG"
                    >
                        Save PNG
                    </button>
                </div>
            )}
        </div>
    );
});


/**
 * MolViewerWithControls - MolViewer with pose selection dropdown
 */
interface DockingPose {
    name: string;
    confidence: number;
    sdfContent: string;
}

interface MolViewerWithControlsProps {
    pdbContent?: string;
    poses: DockingPose[];
    height?: number;
}

export function MolViewerWithControls({
    pdbContent,
    poses,
    height = 400
}: MolViewerWithControlsProps) {
    const [selectedPose, setSelectedPose] = useState<number>(0);

    if (poses.length === 0) {
        return (
            <div className="text-center py-8 text-gray-500">
                No docking results available
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {/* Pose selector */}
            <div className="flex items-center gap-4">
                <label className="font-medium text-gray-700">Docking Pose:</label>
                <select
                    value={selectedPose}
                    onChange={(e) => setSelectedPose(Number(e.target.value))}
                    className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                >
                    {poses.map((pose, idx) => (
                        <option key={idx} value={idx}>
                            {pose.name} (confidence: {pose.confidence.toFixed(2)})
                        </option>
                    ))}
                </select>
            </div>

            {/* 3D Viewer */}
            <MolViewer
                pdbContent={pdbContent}
                sdfContent={poses[selectedPose]?.sdfContent}
                height={height}
            />

            {/* Info */}
            <div className="text-sm text-gray-600">
                Showing {poses.length} ranked poses. Lower confidence score = better binding prediction.
            </div>
        </div>
    );
}

export default MolViewer;
