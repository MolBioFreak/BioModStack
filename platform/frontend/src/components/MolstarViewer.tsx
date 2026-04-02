import React, { useEffect, useState, useRef, useMemo, useCallback } from 'react';
import { ensureMolstarLoaded, isMolstarLoaded, rgbToHex } from '../lib/molstar-loader';

interface Selection {
    chain_id?: string;  // Will be mapped to struct_asym_id
    start_residue_number?: number;
    end_residue_number?: number;
    color?: { r: number; g: number; b: number };
    focus?: boolean;
}

interface Props {
    structureUrl?: string;
    format?: 'cif' | 'pdb';
    alphafoldView?: boolean;  // Deprecated global AF-style color theme; prefer residueColors
    hideControls?: boolean;  // Hide Mol* control panels (for compact view)
    height?: number | string;
    backgroundColor?: string;
    label?: string;  // Optional label to show on viewer
    selections?: Selection[]; // Highlights
    /** Per-residue coloring (e.g., frustration maps). Key format: "A45" or "A:45". */
    residueColors?: Map<string, { r: number; g: number; b: number }>;
}

const parseResidueColorKey = (key: string): { chainId: string; residueNumber: number } | null => {
    const delimited = key.match(/^([^:]+):(-?\d+)$/);
    if (delimited) {
        return { chainId: delimited[1], residueNumber: parseInt(delimited[2], 10) };
    }
    const legacy = key.match(/^([A-Za-z])(\-?\d+)$/);
    if (legacy) {
        return { chainId: legacy[1], residueNumber: parseInt(legacy[2], 10) };
    }
    return null;
};

const buildSelectionSignature = (selections?: Selection[]): string => {
    if (!selections || selections.length === 0) return '';
    return JSON.stringify(
        selections.map((selection) => ({
            chain_id: selection.chain_id ?? '',
            start_residue_number: selection.start_residue_number ?? null,
            end_residue_number: selection.end_residue_number ?? null,
            color: selection.color ?? null,
            focus: Boolean(selection.focus),
        }))
    );
};

const buildResidueColorSignature = (residueColors?: Map<string, { r: number; g: number; b: number }>): string => {
    if (!residueColors || residueColors.size === 0) return '';
    return JSON.stringify(
        Array.from(residueColors.entries())
            .sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey))
            .map(([key, color]) => [key, color.r, color.g, color.b])
    );
};

export default function MolstarViewer({
    structureUrl,
    format = 'pdb',
    alphafoldView = true,
    hideControls = true,
    height = 500,
    backgroundColor = '#0f172a',
    label,
    selections,
    residueColors
}: Props) {
    const [isScriptLoaded, setIsScriptLoaded] = useState(isMolstarLoaded());
    const containerRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<HTMLElement | null>(null);
    const lastAppliedSelectionSignatureRef = useRef<string>('');
    const lastAppliedResidueColorSignatureRef = useRef<string>('');

    // Build absolute URL
    const absoluteUrl = useMemo(() => {
        if (!structureUrl) return null;
        if (structureUrl.startsWith('/')) {
            return `${window.location.origin}${structureUrl}`;
        }
        return structureUrl;
    }, [structureUrl]);
    const selectionSignature = useMemo(() => buildSelectionSignature(selections), [selections]);
    const residueColorSignature = useMemo(() => buildResidueColorSignature(residueColors), [residueColors]);

    // Load the web component script
    useEffect(() => {
        ensureMolstarLoaded().then(() => setIsScriptLoaded(true));
    }, []);

    useEffect(() => {
        lastAppliedSelectionSignatureRef.current = '';
        lastAppliedResidueColorSignatureRef.current = '';
    }, [absoluteUrl]);

    // Apply selections after viewer loads
    const applySelections = useCallback(async () => {
        if (!selections || selections.length === 0) return;
        if (!viewerRef.current) return;
        if (lastAppliedSelectionSignatureRef.current === selectionSignature) return;

        const viewer = viewerRef.current as any;

        // Wait for viewer to be ready
        const waitForReady = async () => {
            for (let i = 0; i < 50; i++) {
                if (viewer.viewerInstance?.visual?.select) {
                    return true;
                }
                await new Promise(r => setTimeout(r, 100));
            }
            return false;
        };

        const ready = await waitForReady();
        if (!ready) {
            return;
        }

        try {
            // Convert our selection format to pdbe-molstar format
            const selectData = selections.map(sel => ({
                struct_asym_id: sel.chain_id,
                auth_asym_id: sel.chain_id,
                start_residue_number: sel.start_residue_number,
                end_residue_number: sel.end_residue_number,
                color: sel.color ? rgbToHex(sel.color.r, sel.color.g, sel.color.b) : undefined,
                focus: sel.focus
            }));

            await viewer.viewerInstance.visual.select({
                data: selectData,
                nonSelectedColor: '#888888'  // Grey out non-selected regions
            });
            lastAppliedSelectionSignatureRef.current = selectionSignature;
        } catch (err) {
            console.error('Failed to apply CDR selections:', err);
        }
    }, [selectionSignature, selections]);

    // Call applySelections when selections change
    useEffect(() => {
        if (isScriptLoaded && selections && selections.length > 0 && selectionSignature) {
            // Delay to allow structure to load
            const timer = setTimeout(applySelections, 1500);
            return () => clearTimeout(timer);
        }
    }, [isScriptLoaded, selectionSignature, applySelections, absoluteUrl, selections]);

    // Apply per-residue coloring (for frustration maps, etc.)
    const applyResidueColors = useCallback(async () => {
        if (!residueColors || residueColors.size === 0) return;
        if (!viewerRef.current) return;
        if (lastAppliedResidueColorSignatureRef.current === residueColorSignature) return;

        const viewer = viewerRef.current as any;

        // Wait for viewer to be ready
        const waitForReady = async () => {
            for (let i = 0; i < 50; i++) {
                if (viewer.viewerInstance?.visual?.select) {
                    return true;
                }
                await new Promise(r => setTimeout(r, 100));
            }
            return false;
        };

        const ready = await waitForReady();
        if (!ready) {
            return;
        }

        try {
            // Convert residueColors map to pdbe-molstar selection format
            const colorData = Array.from(residueColors.entries()).map(([key, color]) => {
                const parsedKey = parseResidueColorKey(key);
                if (!parsedKey) {
                    return null;
                }
                return {
                    struct_asym_id: parsedKey.chainId,
                    auth_asym_id: parsedKey.chainId,
                    start_residue_number: parsedKey.residueNumber,
                    end_residue_number: parsedKey.residueNumber,
                    color: rgbToHex(color.r, color.g, color.b),
                    focus: false
                };
            }).filter(Boolean);

            if (colorData.length > 0) {
                await viewer.viewerInstance.visual.select({
                    data: colorData,
                    nonSelectedColor: '#444444'
                });
                lastAppliedResidueColorSignatureRef.current = residueColorSignature;
            }
        } catch (err) {
            console.error('Failed to apply residue colors:', err);
        }
    }, [residueColorSignature, residueColors]);

    // Call applyResidueColors when residueColors change
    useEffect(() => {
        if (isScriptLoaded && residueColors && residueColors.size > 0 && residueColorSignature) {
            const timer = setTimeout(applyResidueColors, 1500);
            return () => clearTimeout(timer);
        }
    }, [isScriptLoaded, residueColorSignature, applyResidueColors, absoluteUrl, residueColors]);

    // NOTE: Postprocessing settings (shadow, outline, etc) are managed via
    // Molstar's built-in settings panel. Programmatic setProps calls cause
    // WebGL shader corruption after extended use. Do not attempt to override.

    // Parse background color
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

    // Show loading state while script loads
    if (!isScriptLoaded) {
        return (
            <div
                className="w-full flex items-center justify-center bg-slate-900"
                style={{ height: heightStyle }}
            >
                <div className="text-slate-400 flex items-center gap-2">
                    <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    Loading Mol* viewer...
                </div>
            </div>
        );
    }

    if (!absoluteUrl) {
        return (
            <div
                className="w-full flex items-center justify-center text-slate-500 bg-slate-900"
                style={{ height: heightStyle }}
            >
                Select a design to view structure
            </div>
        );
    }

    return (
        <div
            ref={containerRef}
            className="w-full rounded-lg overflow-hidden relative"
            style={{ height: heightStyle }}
        >
            {label && (
                <div className="absolute top-2 left-2 z-10 px-2 py-1 bg-slate-800/80 text-slate-200 text-xs rounded font-medium">
                    {label}
                </div>
            )}
            {React.createElement('pdbe-molstar', {
                key: absoluteUrl, // Force re-mount when URL changes
                ref: (el: HTMLElement) => { viewerRef.current = el; },
                'custom-data-url': absoluteUrl,
                'custom-data-format': format,
                'bg-color-r': bgColor.r.toString(),
                'bg-color-g': bgColor.g.toString(),
                'bg-color-b': bgColor.b.toString(),
                // Avoid the global AlphaFold/pLDDT theme because it bleeds into atom-level
                // representations. pLDDT coloring should come from our residueColors overlay.
                'alphafold-view': residueColors && residueColors.size > 0 ? 'false' : (alphafoldView ? 'true' : 'false'),
                'hide-controls': hideControls ? 'true' : 'false',
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
