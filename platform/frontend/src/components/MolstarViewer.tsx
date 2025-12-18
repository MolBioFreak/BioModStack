import React, { useEffect, useState, useRef, useMemo } from 'react';

interface Props {
    structureUrl?: string;
    format?: 'cif' | 'pdb';
    alphafoldView?: boolean;  // Enable pLDDT coloring
    hideControls?: boolean;  // Hide Mol* control panels (for compact view)
    height?: number | string;
    backgroundColor?: string;
    label?: string;  // Optional label to show on viewer
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

export default function MolstarViewer({
    structureUrl,
    format = 'pdb',
    alphafoldView = true,
    hideControls = true,
    height = 500,
    backgroundColor = '#0f172a',
    label
}: Props) {
    const [isScriptLoaded, setIsScriptLoaded] = useState(scriptLoaded);
    const containerRef = useRef<HTMLDivElement>(null);

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
