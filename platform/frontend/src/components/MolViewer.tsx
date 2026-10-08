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
    colorScheme?: 'rainbow' | 'plddt' | 'chain';
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
    colorScheme = 'plddt'
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

                    // Styling
                    const style: any = {};

                    if (colorScheme === 'plddt') {
                        // AlphaFold pLDDT colors: Very High (Blue), High (Cyan), Low (Yellow), Very Low (Orange)
                        // B-factors in AF2 PDBs are pLDDT scores (0-100)
                        style.cartoon = {
                            colorscheme: {
                                prop: 'b',
                                gradient: new window.$3Dmol.Gradient.Sinebow(0, 100), // Fallback if custom gradient fails
                            }
                        };
                        // Use explicit mapped coloring for accuracy if sinebow isn't desired
                        model.setStyle({}, {
                            cartoon: {
                                colorscheme: {
                                    prop: 'b',
                                    map: window.$3Dmol.Gradient.Rwb
                                }
                            }
                        });
                        // Custom AF2-like coloring manually
                        model.setColorByFunction({}, (atom: any) => {
                            const b = atom.b;
                            if (b >= 90) return '#3b82f6'; // Very High (Blue)
                            if (b >= 70) return '#60a5fa'; // High (Light Blue)
                            if (b >= 50) return '#fbbf24'; // Low (Yellow)
                            return '#f97316'; // Very Low (Orange)
                        });
                    } else if (colorScheme === 'chain') {
                        model.setStyle({}, { cartoon: { colorscheme: 'chain' } });
                    } else {
                        // Rainbow
                        model.setStyle({}, { cartoon: { color: 'spectrum' } });
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
                setLoading(false);

            } catch (e) {
                console.error('MolViewer render error:', e);
                setError('Failed to render structure');
                setLoading(false);
            }
        }
    }, [pdbContent, overlayPdbContent, sdfContent, backgroundColor, showSurface, colorScheme, structureFormat]);

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
