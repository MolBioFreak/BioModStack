/**
 * EpitopeMolstarViewer Component
 * 3D structure viewer for visualizing epitope selections
 * 
 * Features:
 * - Displays uploaded PDB structure in 3D
 * - Highlights selected epitope residues (from 2D selector)
 * - Visualization-only (selection happens in EpitopeSelector)
 */

import React, { useEffect, useState, useRef, useMemo, useCallback } from 'react';

interface Selection {
    chain_id?: string;
    start_residue_number?: number;
    end_residue_number?: number;
    color?: { r: number; g: number; b: number };
    focus?: boolean;
}

interface Props {
    structureUrl?: string;  // URL to PDB file (can be blob URL or API path)
    pdbData?: string;       // Raw PDB content as string
    format?: 'cif' | 'pdb';
    height?: number | string;
    backgroundColor?: string;
    selectedResidues: Set<string>;  // Set of "A45", "B100", etc.
}

// Track if script is loaded globally
let scriptLoaded = false;
let scriptLoading = false;
const loadCallbacks: (() => void)[] = [];

function loadScript(callback: () => void) {
    if (scriptLoaded) {
        callback();
        return;
    }
    loadCallbacks.push(callback);
    if (scriptLoading) return;
    scriptLoading = true;

    // Load CSS
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.type = 'text/css';
    link.href = 'https://cdn.jsdelivr.net/npm/pdbe-molstar@3.4.0/build/pdbe-molstar.css';
    document.head.appendChild(link);

    // Load JS
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/pdbe-molstar@3.4.0/build/pdbe-molstar-component.js';
    script.async = true;
    script.onload = () => {
        console.log('[EpitopeMolstarViewer] pdbe-molstar loaded');
        scriptLoaded = true;
        scriptLoading = false;
        loadCallbacks.forEach(cb => cb());
        loadCallbacks.length = 0;
    };
    script.onerror = (e) => {
        console.error('[EpitopeMolstarViewer] Failed to load pdbe-molstar:', e);
        scriptLoading = false;
    };
    document.head.appendChild(script);
}

function rgbToHex(r: number, g: number, b: number): string {
    return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
}

// Parse residue key like "A45" into { chainId: "A", resNum: 45 }
function parseResidueKey(key: string): { chainId: string; resNum: number } | null {
    const match = key.match(/^([A-Z])(\d+)$/);
    if (!match) return null;
    return { chainId: match[1], resNum: parseInt(match[2], 10) };
}

export default function EpitopeMolstarViewer({
    structureUrl,
    pdbData,
    format = 'pdb',
    height = 400,
    backgroundColor = '#0f172a',
    selectedResidues,
}: Props) {
    const [isScriptLoaded, setIsScriptLoaded] = useState(scriptLoaded);
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<HTMLElement | null>(null);

    // Load script
    useEffect(() => {
        loadScript(() => setIsScriptLoaded(true));
    }, []);

    // Create blob URL from PDB data if provided
    useEffect(() => {
        if (pdbData) {
            const blob = new Blob([pdbData], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            setBlobUrl(url);
            return () => URL.revokeObjectURL(url);
        } else {
            setBlobUrl(null);
        }
    }, [pdbData]);

    // Effective URL for the viewer
    const effectiveUrl = useMemo(() => {
        if (blobUrl) return blobUrl;
        if (!structureUrl) return null;
        if (structureUrl.startsWith('/')) {
            return `${window.location.origin}${structureUrl}`;
        }
        return structureUrl;
    }, [structureUrl, blobUrl]);

    // Convert selected residues to Molstar selection format
    const selections = useMemo((): Selection[] => {
        const result: Selection[] = [];
        const epitopeColor = { r: 16, g: 185, b: 129 };  // Emerald-500

        selectedResidues.forEach(key => {
            const parsed = parseResidueKey(key);
            if (parsed) {
                result.push({
                    chain_id: parsed.chainId,
                    start_residue_number: parsed.resNum,
                    end_residue_number: parsed.resNum,
                    color: epitopeColor,
                });
            }
        });

        return result;
    }, [selectedResidues]);

    // Apply selections to viewer
    const applySelections = useCallback(async () => {
        if (!viewerRef.current) return;
        const viewer = viewerRef.current as any;

        // Wait for viewer to be ready
        for (let i = 0; i < 50; i++) {
            if (viewer.viewerInstance?.visual?.select) break;
            await new Promise(r => setTimeout(r, 100));
        }

        if (!viewer.viewerInstance?.visual?.select) {
            console.warn('[EpitopeMolstarViewer] Viewer not ready');
            return;
        }

        try {
            if (selections.length > 0) {
                const selectData = selections.map(sel => ({
                    struct_asym_id: sel.chain_id,
                    start_residue_number: sel.start_residue_number,
                    end_residue_number: sel.end_residue_number,
                    color: sel.color ? rgbToHex(sel.color.r, sel.color.g, sel.color.b) : undefined,
                }));

                await viewer.viewerInstance.visual.select({
                    data: selectData,
                    nonSelectedColor: '#6b7280',  // Gray-500
                });
            } else {
                // Clear selections - reset to default coloring
                await viewer.viewerInstance.visual.clearSelection();
            }
        } catch (err) {
            console.error('[EpitopeMolstarViewer] Failed to apply selections:', err);
        }
    }, [selections]);

    // Apply selections when they change
    useEffect(() => {
        if (isScriptLoaded && effectiveUrl) {
            const timer = setTimeout(applySelections, 500);
            return () => clearTimeout(timer);
        }
    }, [isScriptLoaded, effectiveUrl, applySelections, selections]);

    // Background color
    const bgColor = useMemo(() => {
        const match = backgroundColor.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
        if (match) {
            return {
                r: parseInt(match[1], 16),
                g: parseInt(match[2], 16),
                b: parseInt(match[3], 16)
            };
        }
        return { r: 15, g: 23, b: 42 };
    }, [backgroundColor]);

    const heightStyle = typeof height === 'number' ? `${height}px` : height;

    if (!isScriptLoaded) {
        return (
            <div
                className="w-full flex items-center justify-center bg-slate-900 rounded-lg"
                style={{ height: heightStyle }}
            >
                <div className="text-slate-400 flex items-center gap-2">
                    <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    Loading 3D viewer...
                </div>
            </div>
        );
    }

    if (!effectiveUrl) {
        return (
            <div
                className="w-full flex items-center justify-center text-slate-500 bg-slate-900 rounded-lg border border-dashed border-slate-700"
                style={{ height: heightStyle }}
            >
                <div className="text-center">
                    <div className="text-4xl mb-2">🧬</div>
                    <div className="text-sm">Upload a PDB to view 3D structure</div>
                </div>
            </div>
        );
    }

    return (
        <div
            ref={containerRef}
            className="w-full rounded-lg overflow-hidden relative border border-slate-700"
            style={{ height: heightStyle }}
        >
            {/* Instructions overlay */}
            <div className="absolute top-2 left-2 z-10 px-2 py-1 bg-slate-800/90 text-slate-300 text-xs rounded flex items-center gap-2">
                <span className="text-blue-400">🔍</span>
                3D Preview - Use 2D grid below to select epitopes
            </div>

            {/* Selection count */}
            {selectedResidues.size > 0 && (
                <div className="absolute top-2 right-2 z-10 px-2 py-1 bg-emerald-600/90 text-white text-xs rounded">
                    {selectedResidues.size} selected
                </div>
            )}

            {React.createElement('pdbe-molstar', {
                ref: (el: HTMLElement) => { viewerRef.current = el; },
                'custom-data-url': effectiveUrl,
                'custom-data-format': format,
                'bg-color-r': bgColor.r.toString(),
                'bg-color-g': bgColor.g.toString(),
                'bg-color-b': bgColor.b.toString(),
                'alphafold-view': 'false',
                'hide-controls': 'true',
                'sequence-panel': 'false',
                'left-panel': 'false',
                'right-panel': 'false',
                'expanded': 'false',
                'landscape': 'true',
                'loading-overlay': 'true',
                'select-interaction': 'true',
                'granularity': 'residue',
                'pdbe-link': 'false',
                style: { width: '100%', height: '100%', display: 'block' }
            })}
        </div>
    );
}

// Named export for convenience
export { EpitopeMolstarViewer };
