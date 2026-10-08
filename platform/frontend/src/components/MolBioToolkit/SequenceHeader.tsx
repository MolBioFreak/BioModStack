/**
 * SequenceHeader - Header bar with sequence metadata and actions
 */

import { ExportDropdown } from './ExportDropdown';
import type { HistoryEntry } from './hooks/useSequenceHistory';
import type { SequenceData } from './types';
import { sequenceUnitLabel } from './utils/nucleotides';

type ViewMode = 'linear' | 'circular' | 'both';

interface SequenceHeaderProps {
    sequenceData: SequenceData;
    onSave?: () => void;
    onUndo?: () => void;
    onRedo?: () => void;
    onAutoAnnotate?: () => void;
    canUndo?: boolean;
    canRedo?: boolean;
    isDirty?: boolean;
    loading?: boolean;
    isAnnotating?: boolean;
    viewMode?: ViewMode;
    onViewModeChange?: (mode: ViewMode) => void;
    showGCTrack?: boolean;
    onGCTrackToggle?: () => void;
    onOpenLibrary?: () => void;
    isViewerFullscreen?: boolean;
    onToggleFullscreen?: () => void;
    isLibraryPanelCollapsed?: boolean;
    isToolPanelCollapsed?: boolean;
    onToggleLibraryPanel?: () => void;
    onToggleToolPanel?: () => void;
    historyJournal?: HistoryEntry[];
}

function calculateGC(sequence: string): number {
    if (!sequence) return 0;
    const gc = (sequence.match(/[GC]/gi) || []).length;
    return Math.round((gc / sequence.length) * 100);
}

export function SequenceHeader({
    sequenceData,
    onSave,
    onUndo,
    onRedo,
    onAutoAnnotate,
    canUndo = false,
    canRedo = false,
    isDirty = false,
    loading = false,
    isAnnotating = false,
    viewMode = 'both',
    onViewModeChange,
    showGCTrack = true,
    onGCTrackToggle,
    onOpenLibrary,
    isViewerFullscreen = false,
    onToggleFullscreen,
    isLibraryPanelCollapsed = false,
    isToolPanelCollapsed = false,
    onToggleLibraryPanel,
    onToggleToolPanel,
    historyJournal = [],
}: SequenceHeaderProps) {
    const gcContent = calculateGC(sequenceData.sequence);
    const unitLabel = sequenceUnitLabel(sequenceData.sequenceType === 'rna' ? 'rna' : 'dna');

    return (
        <div className="sequence-header flex items-center justify-between px-4 py-2 bg-slate-800 border-b border-slate-700">
            {/* Left: Sequence info */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                    <h2 className="text-lg font-semibold text-slate-100">
                        {sequenceData.name}
                        {isDirty && <span className="text-blue-400 ml-1">*</span>}
                    </h2>
                    {sequenceData.circular && (
                        <span className="px-2 py-0.5 text-xs bg-emerald-900/50 text-emerald-400 rounded">
                            Circular
                        </span>
                    )}
                    <span className="px-2 py-0.5 text-xs bg-slate-700 text-slate-300 rounded uppercase">
                        {sequenceData.sequenceType}
                    </span>
                </div>

                <div className="flex items-center gap-3 text-sm text-slate-400">
                    <span>{sequenceData.sequence.length.toLocaleString()} {unitLabel}</span>
                    <span>•</span>
                    <span>GC: {gcContent}%</span>
                    <span>•</span>
                    <span>{sequenceData.features.length} features</span>
                    {sequenceData.primers && sequenceData.primers.length > 0 && (
                        <>
                            <span>•</span>
                            <span>{sequenceData.primers.length} primers</span>
                        </>
                    )}
                    {sequenceData.version != null && (
                        <>
                            <span>•</span>
                            <span>v{sequenceData.version}</span>
                        </>
                    )}
                    {sequenceData.operation && (
                        <>
                            <span>•</span>
                            <span>{sequenceData.operation.replace(/_/g, ' ')}</span>
                        </>
                    )}
                </div>
            </div>

            {/* Right: Actions */}
            <div className="flex items-center gap-2">
                {onOpenLibrary && (
                    <button
                        onClick={onOpenLibrary}
                        className="flex items-center gap-1 px-2 py-1.5 hover:bg-slate-700 rounded text-sm text-slate-300 transition-colors"
                        title="Open molecular library, import, or build a construct"
                    >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                        </svg>
                        Acquire
                    </button>
                )}

                {/* Undo/Redo */}
                <div className="flex items-center border-r border-slate-600 pr-2 mr-2">
                    <button
                        onClick={onUndo}
                        disabled={!canUndo}
                        className="p-1.5 hover:bg-slate-700 rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                        title="Undo (Ctrl+Z)"
                    >
                        <svg className="w-4 h-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6" />
                        </svg>
                    </button>
                    <button
                        onClick={onRedo}
                        disabled={!canRedo}
                        className="p-1.5 hover:bg-slate-700 rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                        title="Redo (Ctrl+Shift+Z)"
                    >
                        <svg className="w-4 h-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 10h-10a8 8 0 00-8 8v2M21 10l-6 6m6-6l-6-6" />
                        </svg>
                    </button>
                </div>

                {/* View Mode Toggle (only show for circular sequences) */}
                {sequenceData.circular && onViewModeChange && (
                    <div className="flex items-center border-r border-slate-600 pr-2 mr-2">
                        <div className="flex rounded overflow-hidden border border-slate-600">
                            <button
                                onClick={() => onViewModeChange('linear')}
                                className={`px-2 py-1 text-xs transition-colors ${viewMode === 'linear'
                                    ? 'bg-accent text-white'
                                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                                    }`}
                                title="Linear view only"
                            >
                                Linear
                            </button>
                            <button
                                onClick={() => onViewModeChange('both')}
                                className={`px-2 py-1 text-xs border-x border-slate-600 transition-colors ${viewMode === 'both'
                                    ? 'bg-accent text-white'
                                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                                    }`}
                                title="Both views split"
                            >
                                Both
                            </button>
                            <button
                                onClick={() => onViewModeChange('circular')}
                                className={`px-2 py-1 text-xs transition-colors ${viewMode === 'circular'
                                    ? 'bg-accent text-white'
                                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                                    }`}
                                title="Circular view only"
                            >
                                Circular
                            </button>
                        </div>
                    </div>
                )}

                {(onToggleLibraryPanel || onToggleToolPanel) && (
                    <div className="flex items-center border-r border-slate-600 pr-2 mr-2 gap-2">
                        {onToggleLibraryPanel && (
                            <button
                                type="button"
                                onClick={onToggleLibraryPanel}
                                className={`flex items-center gap-1 rounded px-2 py-1.5 text-sm transition-colors ${isLibraryPanelCollapsed
                                        ? 'text-blue-300 hover:bg-slate-700'
                                        : 'text-slate-300 hover:bg-slate-700'
                                    }`}
                                title={isLibraryPanelCollapsed ? 'Show construct shelf' : 'Hide construct shelf'}
                            >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={isLibraryPanelCollapsed ? 'M9 5l7 7-7 7' : 'M15 19l-7-7 7-7'} />
                                </svg>
                                {isLibraryPanelCollapsed ? 'Show Shelf' : 'Hide Shelf'}
                            </button>
                        )}
                        {onToggleToolPanel && (
                            <button
                                type="button"
                                onClick={onToggleToolPanel}
                                className={`flex items-center gap-1 rounded px-2 py-1.5 text-sm transition-colors ${isToolPanelCollapsed
                                        ? 'text-blue-300 hover:bg-slate-700'
                                        : 'text-slate-300 hover:bg-slate-700'
                                    }`}
                                title={isToolPanelCollapsed ? 'Show toolkit tools' : 'Hide toolkit tools'}
                            >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={isToolPanelCollapsed ? 'M15 5l-7 7 7 7' : 'M9 19l7-7-7-7'} />
                                </svg>
                                {isToolPanelCollapsed ? 'Show Tools' : 'Hide Tools'}
                            </button>
                        )}
                    </div>
                )}

                {sequenceData.circular && onToggleFullscreen && (
                    <button
                        type="button"
                        onClick={onToggleFullscreen}
                        className={`flex items-center gap-1 rounded px-2 py-1.5 text-sm transition-colors border-r border-slate-600 mr-2 pr-3 ${isViewerFullscreen
                                ? 'text-blue-300 hover:bg-slate-700'
                                : 'text-slate-300 hover:bg-slate-700'
                            }`}
                        title={isViewerFullscreen ? 'Exit focused plasmid view' : 'Focus Viewer'}
                    >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={isViewerFullscreen ? 'M9 9H5v4m10-4h4v4M9 15H5v-4m10 4h4v-4' : 'M8 3H5a2 2 0 00-2 2v3m14-5h2a2 2 0 012 2v3M8 21H5a2 2 0 01-2-2v-3m14 5h2a2 2 0 002-2v-3'} />
                        </svg>
                        {isViewerFullscreen ? 'Exit Focus' : 'Focus Viewer'}
                    </button>
                )}

                {/* Analytics Track Toggle */}
                {onGCTrackToggle && (
                    <button
                        onClick={onGCTrackToggle}
                        className={`flex items-center gap-1 px-2 py-1.5 rounded text-sm transition-colors border-r border-slate-600 mr-2 pr-3 ${showGCTrack
                                ? 'text-emerald-400 hover:bg-slate-700'
                                : 'text-slate-500 hover:bg-slate-700'
                            }`}
                        title={showGCTrack ? 'Hide analytics tracks' : 'Show analytics tracks'}
                    >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                        </svg>
                        Tracks
                    </button>
                )}

                {/* Auto-Annotate */}
                {onAutoAnnotate && (
                    <button
                        onClick={onAutoAnnotate}
                        disabled={isAnnotating || !sequenceData.sequence || sequenceData.sequenceType === 'rna'}
                        className="flex items-center gap-1 px-2 py-1.5 hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed rounded text-sm text-slate-300 transition-colors"
                        title={sequenceData.sequenceType === 'rna'
                            ? 'Auto-annotation is currently DNA/plasmid-focused'
                            : 'Auto-detect common plasmid features (promoters, ori, resistance genes)'}
                    >
                        {isAnnotating ? (
                            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                        ) : (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
                            </svg>
                        )}
                        Auto-Annotate
                    </button>
                )}

                {/* Export */}
                <ExportDropdown sequenceData={sequenceData} historyJournal={historyJournal} />

                {/* Save */}
                {onSave && (
                    <button
                        onClick={onSave}
                        disabled={loading || !isDirty}
                        className="flex items-center gap-1 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm text-white transition-colors"
                    >
                        {loading ? (
                            <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                        ) : (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
                            </svg>
                        )}
                        Save
                    </button>
                )}
            </div>
        </div>
    );
}
