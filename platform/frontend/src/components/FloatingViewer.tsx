/**
 * FloatingViewer - A draggable, resizable floating window for structure comparison
 * 
 * Uses react-rnd for drag and resize functionality.
 */

import { useState, useRef, useEffect } from 'react';
import { Rnd } from 'react-rnd';
import MolstarViewer from './MolstarViewer';

interface FloatingViewerProps {
    structureUrl: string;
    format?: 'pdb' | 'cif';
    label: string;
    onClose: () => void;
    initialPosition?: { x: number; y: number };
}

export default function FloatingViewer({
    structureUrl,
    format = 'pdb',
    label,
    onClose,
    initialPosition = { x: 20, y: 20 }
}: FloatingViewerProps) {
    const [isMinimized, setIsMinimized] = useState(false);
    // Start with provided position or default
    const [position, setPosition] = useState(initialPosition);
    const [size, setSize] = useState({ width: 350, height: 320 });
    const rndRef = useRef<Rnd>(null);
    const [isReady, setIsReady] = useState(false);

    // Initialize after mount to get proper positioning
    useEffect(() => {
        // Small delay to ensure parent is rendered
        const timer = setTimeout(() => {
            setIsReady(true);
        }, 100);
        return () => clearTimeout(timer);
    }, []);

    // Minimized view - just a small pill
    if (isMinimized) {
        return (
            <div
                className="absolute bottom-4 right-4 z-50 flex items-center gap-2 px-3 py-2 bg-slate-800/95 border border-slate-600 rounded-lg shadow-xl cursor-pointer hover:bg-slate-700/95 transition-colors"
                onClick={() => setIsMinimized(false)}
            >
                <div className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
                <span className="text-xs text-slate-200 font-medium truncate max-w-[150px]">
                    {label}
                </span>
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        onClose();
                    }}
                    className="text-slate-400 hover:text-red-400 transition-colors"
                    title="Close comparison"
                >
                    ✕
                </button>
            </div>
        );
    }

    // Wait for mount
    if (!isReady) {
        return null;
    }

    return (
        <Rnd
            ref={rndRef}
            default={{
                x: position.x,
                y: position.y,
                width: size.width,
                height: size.height,
            }}
            position={position}
            size={size}
            onDragStop={(_e, d) => {
                setPosition({ x: d.x, y: d.y });
            }}
            onResizeStop={(_e, _direction, ref, _delta, pos) => {
                setSize({
                    width: parseInt(ref.style.width),
                    height: parseInt(ref.style.height)
                });
                setPosition(pos);
            }}
            minWidth={250}
            minHeight={200}
            maxWidth={800}
            maxHeight={700}
            bounds="parent"
            dragHandleClassName="floating-viewer-handle"
            className="z-40"
            style={{
                display: 'flex',
                flexDirection: 'column',
            }}
        >
            <div className="w-full h-full flex flex-col bg-slate-900 border border-slate-600 rounded-lg shadow-2xl overflow-hidden">
                {/* Title Bar - Drag Handle */}
                <div className="floating-viewer-handle flex items-center justify-between px-3 py-2 bg-slate-800 border-b border-slate-700 cursor-move select-none">
                    <div className="flex items-center gap-2 overflow-hidden">
                        <span className="text-xs text-slate-400">≡</span>
                        <span className="text-xs text-slate-200 font-medium truncate">
                            {label}
                        </span>
                    </div>
                    <div className="flex items-center gap-1">
                        {/* Minimize button */}
                        <button
                            onClick={() => setIsMinimized(true)}
                            className="w-5 h-5 flex items-center justify-center text-slate-400 hover:text-slate-200 hover:bg-slate-700 rounded transition-colors"
                            title="Minimize"
                        >
                            <span className="text-xs">−</span>
                        </button>
                        {/* Close button */}
                        <button
                            onClick={onClose}
                            className="w-5 h-5 flex items-center justify-center text-slate-400 hover:text-red-400 hover:bg-slate-700 rounded transition-colors"
                            title="Close"
                        >
                            <span className="text-xs">✕</span>
                        </button>
                    </div>
                </div>

                {/* Viewer Content */}
                <div className="flex-1 relative overflow-hidden">
                    <MolstarViewer
                        structureUrl={structureUrl}
                        format={format}
                        alphafoldView={false}
                        hideControls={true}
                        height="100%"
                        backgroundColor="#0f172a"
                    />
                </div>

                {/* Resize indicator in corner */}
                <div className="absolute bottom-0 right-0 w-3 h-3 cursor-se-resize opacity-30 hover:opacity-60">
                    <svg viewBox="0 0 10 10" className="w-full h-full text-slate-400">
                        <path d="M0,10 L10,0 M4,10 L10,4 M8,10 L10,8" stroke="currentColor" strokeWidth="1.5" fill="none" />
                    </svg>
                </div>
            </div>
        </Rnd>
    );
}
