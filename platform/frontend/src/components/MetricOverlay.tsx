/**
 * MetricOverlay - Draggable, resizable, semi-transparent floating panel for analytics
 * 
 * Uses react-rnd for drag/resize. Shows toggleable metric views.
 */

import { useState, useEffect } from 'react';
import { Rnd } from 'react-rnd';
import { SparklineChart, IPTMHeatmap } from './MetricCharts';
import { PAEHeatmap } from './PAEHeatmap';

type MetricType = 'structure' | 'pae' | 'plddt' | 'iptm';

interface MetricOverlayProps {
    designId: string | null;
    residueData?: {
        residue_numbers: number[];
        plddt: number[];
    };
    initialPosition: { x: number; y: number };
    initialType: MetricType;
    structureAnalysis?: {
        residue_count: number;
        chain_ids: string[];
        gyration_radius: number | null;
        secondary_structure: { helix: number; sheet: number; coil: number };
    };
    availableTypes?: MetricType[];
    pairChainsIptm?: Record<string, Record<string, number>>; // For ipTM heatmap
}

export function MetricOverlay({
    designId,
    residueData,
    initialPosition,
    initialType,
    structureAnalysis,
    availableTypes = ['structure', 'pae', 'plddt', 'iptm'],
    pairChainsIptm
}: MetricOverlayProps) {
    const [isMinimized, setIsMinimized] = useState(false);
    const [isHovered, setIsHovered] = useState(false);
    const [metricType, setMetricType] = useState<MetricType>(initialType);
    const [position, setPosition] = useState(initialPosition);
    const [size, setSize] = useState({ width: 180, height: 120 });
    const [isReady, setIsReady] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => setIsReady(true), 100);
        return () => clearTimeout(timer);
    }, []);

    // Initial size only (don't auto-resize on type change - let user control)

    const typeLabels: Record<MetricType, string> = {
        structure: '📐',
        pae: '🔲',
        plddt: '📊',
        iptm: '🔗',
    };

    const typeNames: Record<MetricType, string> = {
        structure: 'Info',
        pae: 'PAE',
        plddt: 'pLDDT',
        iptm: 'ipTM',
    };

    const cycleType = () => {
        const currentIndex = availableTypes.indexOf(metricType);
        const nextIndex = (currentIndex + 1) % availableTypes.length;
        setMetricType(availableTypes[nextIndex]);
    };

    // Minimized pill
    if (isMinimized) {
        return (
            <div
                className="absolute z-40"
                style={{ left: position.x, top: position.y }}
            >
                <button
                    onClick={() => setIsMinimized(false)}
                    className="px-2 py-1 bg-slate-800/80 backdrop-blur-sm border border-slate-600/50 rounded text-xs text-slate-300 hover:bg-slate-700/90 transition-all flex items-center gap-1"
                >
                    {typeLabels[metricType]} {typeNames[metricType]}
                </button>
            </div>
        );
    }

    if (!isReady) return null;

    return (
        <Rnd
            position={position}
            size={size}
            onDragStop={(_e, d) => setPosition({ x: d.x, y: d.y })}
            onResizeStop={(_e, _dir, ref, _delta, pos) => {
                setSize({ width: ref.offsetWidth, height: ref.offsetHeight });
                setPosition(pos);
            }}
            dragHandleClassName="metric-drag-handle"
            bounds="parent"
            minWidth={120}
            minHeight={80}
            maxWidth={400}
            maxHeight={350}
            className="z-40"
            enableResizing={{
                bottom: true,
                bottomRight: true,
                right: true,
            }}
        >
            <div
                onMouseEnter={() => setIsHovered(true)}
                onMouseLeave={() => setIsHovered(false)}
                style={{ opacity: isHovered ? 0.95 : 0.5, width: '100%', height: '100%' }}
                className="transition-opacity duration-200"
            >
                <div className="bg-slate-900/90 backdrop-blur-sm border border-slate-600/50 rounded-lg shadow-xl overflow-hidden h-full flex flex-col">
                    {/* Compact Header - Drag Handle */}
                    <div className="metric-drag-handle flex items-center justify-between px-2 py-1 border-b border-slate-700/50 cursor-move select-none bg-slate-800/60 shrink-0">
                        <button
                            onClick={cycleType}
                            className="text-xs text-slate-300 hover:text-white transition-colors flex items-center gap-1"
                            title="Click to switch metrics"
                        >
                            {typeLabels[metricType]} <span className="text-slate-500">◀▶</span>
                        </button>
                        <span className="text-[10px] text-slate-500">{typeNames[metricType]}</span>
                        <button
                            onClick={() => setIsMinimized(true)}
                            className="text-slate-400 hover:text-white text-sm px-1"
                            title="Minimize"
                        >
                            −
                        </button>
                    </div>

                    {/* Content - Fills remaining space */}
                    <div className="flex-1 p-1 overflow-hidden">
                        {metricType === 'plddt' && (
                            <>
                                {residueData && residueData.plddt.length > 0 ? (
                                    <SparklineChart
                                        data={residueData.plddt}
                                        width={size.width - 10}
                                        height={size.height - 35}
                                        color="#60a5fa"
                                    />
                                ) : (
                                    <div className="h-full flex items-center justify-center text-xs text-slate-500">
                                        No pLDDT data
                                    </div>
                                )}
                            </>
                        )}

                        {metricType === 'pae' && designId && (
                            <div className="h-full flex items-center justify-center">
                                <PAEHeatmap
                                    designId={designId}
                                    width={Math.min(size.width - 10, size.height - 35)}
                                    height={size.height - 35}
                                />
                            </div>
                        )}

                        {metricType === 'iptm' && (
                            <div className="h-full flex items-center justify-center">
                                {pairChainsIptm && Object.keys(pairChainsIptm).length > 0 ? (
                                    <IPTMHeatmap
                                        data={pairChainsIptm}
                                        width={size.width - 10}
                                        height={size.height - 35}
                                    />
                                ) : (
                                    <div className="text-xs text-slate-500">
                                        No chain interface data
                                    </div>
                                )}
                            </div>
                        )}

                        {metricType === 'structure' && (
                            <>
                                {structureAnalysis ? (
                                    <div className="text-[11px] space-y-1 p-1">
                                        <div className="flex justify-between">
                                            <span className="text-slate-500">Res</span>
                                            <span className="text-white font-mono">{structureAnalysis.residue_count}</span>
                                        </div>
                                        <div className="flex justify-between">
                                            <span className="text-slate-500">Chains</span>
                                            <span className="text-cyan-400 font-mono">{structureAnalysis.chain_ids.join(', ')}</span>
                                        </div>
                                        {structureAnalysis.gyration_radius && (
                                            <div className="flex justify-between">
                                                <span className="text-slate-500">RoG</span>
                                                <span className="text-purple-400 font-mono">{structureAnalysis.gyration_radius.toFixed(1)}Å</span>
                                            </div>
                                        )}
                                        {/* SSE Bar */}
                                        <div className="pt-1 border-t border-slate-700/50 mt-1">
                                            <div className="flex gap-0.5 h-2 rounded overflow-hidden">
                                                {(() => {
                                                    const sse = structureAnalysis.secondary_structure;
                                                    const total = sse.helix + sse.sheet + sse.coil;
                                                    if (total === 0) return <div className="flex-1 bg-slate-600" />;
                                                    return (
                                                        <>
                                                            <div
                                                                className="bg-red-500"
                                                                style={{ width: `${(sse.helix / total) * 100}%` }}
                                                                title={`α: ${sse.helix}`}
                                                            />
                                                            <div
                                                                className="bg-yellow-500"
                                                                style={{ width: `${(sse.sheet / total) * 100}%` }}
                                                                title={`β: ${sse.sheet}`}
                                                            />
                                                            <div
                                                                className="bg-slate-500"
                                                                style={{ width: `${(sse.coil / total) * 100}%` }}
                                                                title={`coil: ${sse.coil}`}
                                                            />
                                                        </>
                                                    );
                                                })()}
                                            </div>
                                            <div className="flex justify-between text-[8px] text-slate-500 mt-0.5">
                                                <span className="text-red-400">α</span>
                                                <span className="text-yellow-400">β</span>
                                                <span>coil</span>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="h-full flex items-center justify-center text-xs text-slate-500">
                                        Loading...
                                    </div>
                                )}
                            </>
                        )}
                    </div>

                    {/* Resize handle indicator */}
                    <div className="absolute bottom-0 right-0 w-3 h-3 cursor-se-resize opacity-30 hover:opacity-60 transition-opacity">
                        <svg viewBox="0 0 10 10" className="w-full h-full text-slate-400">
                            <path d="M10 0 L10 10 L0 10" fill="currentColor" />
                        </svg>
                    </div>
                </div>
            </div>
        </Rnd>
    );
}

export default MetricOverlay;
