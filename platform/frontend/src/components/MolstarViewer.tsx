import React, { useEffect, useState, useRef, useMemo, useCallback } from 'react';

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

// Track if script is loaded globally to avoid multiple loads
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

    // Load CSS first
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
        console.log('pdbe-molstar web component script loaded');
        scriptLoaded = true;
        scriptLoading = false;
        loadCallbacks.forEach(cb => cb());
        loadCallbacks.length = 0;
    };
    script.onerror = (e) => {
        console.error('Failed to load pdbe-molstar script:', e);
        scriptLoading = false;
    };
    document.head.appendChild(script);
}

// Convert RGB to hex
function rgbToHex(r: number, g: number, b: number): string {
    return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
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
    const [isScriptLoaded, setIsScriptLoaded] = useState(scriptLoaded);
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
        loadScript(() => setIsScriptLoaded(true));
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
