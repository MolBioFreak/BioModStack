/**
 * EpitopeMolstarViewer Component
 * 3D structure viewer for visualizing and selecting epitope residues
 * 
 * Features:
 * - Displays uploaded PDB structure in 3D
 * - Highlights selected epitope residues (synced from 2D selector)
 * - Click-to-select: clicking residues in 3D syncs back to 2D grid
 * - Uses shared Molstar loader for efficient script management
 */

import React, { useEffect, useState, useRef, useMemo, useCallback } from 'react';
import {
    ensureMolstarLoaded,
    isMolstarLoaded,
    rgbToHex,
    parseResidueKey
} from '../lib/molstar-loader';

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
    /** Callback when a residue is clicked in 3D view (for bidirectional sync) */
    onResidueClick?: (residueKey: string) => void;
}

export default function EpitopeMolstarViewer({
    structureUrl,
    pdbData,
    format = 'pdb',
    height = 400,
    backgroundColor = '#0f172a',
    selectedResidues,
    onResidueClick,
}: Props) {
    const [isScriptLoaded, setIsScriptLoaded] = useState(isMolstarLoaded());
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<HTMLElement | null>(null);

    // Load script using shared loader
    useEffect(() => {
        ensureMolstarLoaded().then(() => setIsScriptLoaded(true));
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

    // Handle click events from Molstar for 3D→2D sync
    useEffect(() => {
        if (!onResidueClick || !isScriptLoaded) return;

        const handleClick = (event: Event) => {
            const detail = (event as CustomEvent).detail;
            // Extract residue info from click event
            // PDBe Molstar click events include residue and chain data
            const chainId = detail?.authChainId || detail?.chainId;
            const residueNumber = detail?.residueNumber;

            if (chainId && residueNumber !== undefined) {
                const residueKey = `${chainId}${residueNumber}`;
                console.log('[EpitopeMolstarViewer] 3D Click:', residueKey);
                onResidueClick(residueKey);
            }
        };

        document.addEventListener('PDB.molstar.click', handleClick);
        return () => document.removeEventListener('PDB.molstar.click', handleClick);
    }, [onResidueClick, isScriptLoaded]);

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
                key: effectiveUrl,
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
