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
    alphafoldView?: boolean;  // Enable pLDDT coloring
    hideControls?: boolean;  // Hide Mol* control panels (for compact view)
    height?: number | string;
    backgroundColor?: string;
    label?: string;  // Optional label to show on viewer
    selections?: Selection[]; // Highlights
    /** Per-residue coloring (e.g., frustration maps). Key format: "A45" (chain + residue number) */
    residueColors?: Map<string, { r: number; g: number; b: number }>;
}

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

    // Build absolute URL
    const absoluteUrl = useMemo(() => {
        if (!structureUrl) return null;
        if (structureUrl.startsWith('/')) {
            return `${window.location.origin}${structureUrl}`;
        }
        return structureUrl;
    }, [structureUrl]);

    // Load the web component script
    useEffect(() => {
        ensureMolstarLoaded().then(() => setIsScriptLoaded(true));
    }, []);

    // Apply selections after viewer loads
    const applySelections = useCallback(async () => {
        if (!selections || selections.length === 0) return;
        if (!viewerRef.current) return;

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
            console.warn('MolstarViewer: viewer.viewerInstance not ready after 5s');
            return;
        }

        try {
            // Convert our selection format to pdbe-molstar format
            const selectData = selections.map(sel => ({
                struct_asym_id: sel.chain_id,
                start_residue_number: sel.start_residue_number,
                end_residue_number: sel.end_residue_number,
                color: sel.color ? rgbToHex(sel.color.r, sel.color.g, sel.color.b) : undefined,
                focus: sel.focus
            }));

            await viewer.viewerInstance.visual.select({
                data: selectData,
                nonSelectedColor: '#888888'  // Grey out non-selected regions
            });
            console.log('CDR selections applied:', selectData);
        } catch (err) {
            console.error('Failed to apply CDR selections:', err);
        }
    }, [selections]);

    // Call applySelections when selections change
    useEffect(() => {
        if (isScriptLoaded && selections && selections.length > 0) {
            // Delay to allow structure to load
            const timer = setTimeout(applySelections, 1500);
            return () => clearTimeout(timer);
        }
    }, [isScriptLoaded, selections, applySelections, absoluteUrl]);

    // Apply per-residue coloring (for frustration maps, etc.)
    const applyResidueColors = useCallback(async () => {
        if (!residueColors || residueColors.size === 0) return;
        if (!viewerRef.current) return;

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
            console.warn('MolstarViewer: viewer not ready for residue coloring');
            return;
        }

        try {
            // Convert residueColors map to pdbe-molstar selection format
            const colorData = Array.from(residueColors.entries()).map(([key, color]) => {
                // Parse key like "A45" into chain and residue number
                const match = key.match(/^([A-Za-z])(-?\d+)$/);
                if (!match) {
                    console.warn(`Invalid residue key format: ${key}`);
                    return null;
                }
                return {
                    struct_asym_id: match[1],
                    start_residue_number: parseInt(match[2]),
                    end_residue_number: parseInt(match[2]),
                    color: rgbToHex(color.r, color.g, color.b),
                    focus: false
                };
            }).filter(Boolean);

            if (colorData.length > 0) {
                await viewer.viewerInstance.visual.select({
                    data: colorData,
                    nonSelectedColor: '#444444'
                });
                console.log(`Applied ${colorData.length} residue colors`);
            }
        } catch (err) {
            console.error('Failed to apply residue colors:', err);
        }
    }, [residueColors]);

    // Call applyResidueColors when residueColors change
    useEffect(() => {
        if (isScriptLoaded && residueColors && residueColors.size > 0) {
            const timer = setTimeout(applyResidueColors, 1500);
            return () => clearTimeout(timer);
        }
    }, [isScriptLoaded, residueColors, applyResidueColors, absoluteUrl]);

    // Apply viewport rendering defaults (shadows OFF, outlines ON)
    // This runs once after structure loads to set user-preferred defaults
    useEffect(() => {
        if (!isScriptLoaded || !absoluteUrl) return;

        const applyDefaults = async () => {
            if (!viewerRef.current) return;
            const viewer = viewerRef.current as any;

            // Wait for plugin to be fully initialized (longer wait for stability)
            for (let i = 0; i < 60; i++) {
                const plugin = viewer.viewerInstance?.plugin;
                if (plugin?.canvas3d?.props) {
                    try {
                        // Use the Mol* plugin's setProps with correct structure
                        // Only modify shadow - keep outline at Molstar's default (which is on)
                        plugin.canvas3d.setProps({
                            postprocessing: {
                                ...plugin.canvas3d.props.postprocessing,
                                shadow: { name: 'off', params: {} },
                            },
                        });
                        console.log('Molstar defaults applied: shadows OFF');
                        return;
                    } catch (err) {
                        // Silently fail - user can adjust in Molstar settings
                        console.debug('Could not apply viewport defaults:', err);
                        return;
                    }
                }
                await new Promise(r => setTimeout(r, 100));
            }
        };

        // Delay to allow structure to load first
        const timer = setTimeout(applyDefaults, 3000);
        return () => clearTimeout(timer);
    }, [isScriptLoaded, absoluteUrl]);

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
                'alphafold-view': alphafoldView ? 'true' : 'false',
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
