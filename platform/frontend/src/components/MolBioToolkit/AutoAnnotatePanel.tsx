/**
 * AutoAnnotatePanel - Settings panel for pLannotate auto-annotation
 *
 * Exposes all pLannotate configuration options to the user.
 */

import { useState } from 'react';

export interface AutoAnnotateSettings {
    minIdentity: number;      // Minimum percent identity threshold (0-100)
    detailed: boolean;        // Use detailed search mode (more hits, more false positives)
    filterFragments: boolean; // Filter out partial/fragment matches
}

const DEFAULT_SETTINGS: AutoAnnotateSettings = {
    minIdentity: 35,       // Lower threshold catches more features
    detailed: true,        // Detailed search by default for better detection
    filterFragments: false
};

interface AutoAnnotatePanelProps {
    isOpen: boolean;
    onClose: () => void;
    onAnnotate: (settings: AutoAnnotateSettings) => void;
    isAnnotating: boolean;
    hasSequence: boolean;
    sequenceLength: number;
    isCircular: boolean;
}

export function AutoAnnotatePanel({
    isOpen,
    onClose,
    onAnnotate,
    isAnnotating,
    hasSequence,
    sequenceLength,
    isCircular
}: AutoAnnotatePanelProps) {
    const [settings, setSettings] = useState<AutoAnnotateSettings>(DEFAULT_SETTINGS);

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
            <div
                className="bg-slate-800 rounded-lg shadow-xl w-full max-w-md p-6 border border-slate-600"
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
                        <svg className="w-5 h-5 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
                        </svg>
                        Auto-Annotate Settings
                    </h3>
                    <button
                        onClick={onClose}
                        className="p-1 hover:bg-slate-700 rounded transition-colors"
                    >
                        <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Sequence Info */}
                <div className="mb-4 p-3 bg-slate-900 rounded text-sm text-slate-400">
                    <div className="flex justify-between">
                        <span>Sequence Length:</span>
                        <span className="text-slate-200">{sequenceLength.toLocaleString()} bp</span>
                    </div>
                    <div className="flex justify-between mt-1">
                        <span>Topology:</span>
                        <span className={isCircular ? "text-emerald-400" : "text-slate-200"}>
                            {isCircular ? "Circular" : "Linear"}
                        </span>
                    </div>
                </div>

                {/* Settings */}
                <div className="space-y-4">
                    {/* Minimum Identity Slider */}
                    <div>
                        <label className="flex items-center justify-between text-sm text-slate-300 mb-2">
                            <span>Minimum Identity</span>
                            <span className="text-accent font-mono">{settings.minIdentity}%</span>
                        </label>
                        <input
                            type="range"
                            min="20"
                            max="99"
                            value={settings.minIdentity}
                            onChange={e => setSettings({ ...settings, minIdentity: parseInt(e.target.value) })}
                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-accent"
                        />
                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                            <span>More hits</span>
                            <span>Fewer false positives</span>
                        </div>
                    </div>

                    {/* Detailed Mode Toggle */}
                    <div className="flex items-center justify-between p-3 bg-slate-900 rounded">
                        <div>
                            <div className="text-sm text-slate-300">Detailed Search</div>
                            <div className="text-xs text-slate-500">More thorough but slower, may have more false positives</div>
                        </div>
                        <button
                            onClick={() => setSettings({ ...settings, detailed: !settings.detailed })}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${settings.detailed ? 'bg-accent' : 'bg-slate-600'
                                }`}
                        >
                            <span
                                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${settings.detailed ? 'translate-x-6' : 'translate-x-1'
                                    }`}
                            />
                        </button>
                    </div>

                    {/* Filter Fragments Toggle */}
                    <div className="flex items-center justify-between p-3 bg-slate-900 rounded">
                        <div>
                            <div className="text-sm text-slate-300">Filter Fragments</div>
                            <div className="text-xs text-slate-500">Exclude partial feature matches</div>
                        </div>
                        <button
                            onClick={() => setSettings({ ...settings, filterFragments: !settings.filterFragments })}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${settings.filterFragments ? 'bg-accent' : 'bg-slate-600'
                                }`}
                        >
                            <span
                                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${settings.filterFragments ? 'translate-x-6' : 'translate-x-1'
                                    }`}
                            />
                        </button>
                    </div>
                </div>

                {/* Info */}
                <div className="mt-4 p-3 bg-blue-900/30 border border-blue-800 rounded text-xs text-blue-300">
                    <strong>Databases:</strong> SnapGene, SwissProt, UniProt
                    <br />
                    <strong>Detects:</strong> Origins (ori), Resistance genes (KanR, AmpR), Promoters, Tags, CDS
                </div>

                {/* Actions */}
                <div className="flex justify-end gap-2 mt-6">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-sm text-slate-300 hover:bg-slate-700 rounded transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => onAnnotate(settings)}
                        disabled={isAnnotating || !hasSequence}
                        className="flex items-center gap-2 px-4 py-2 text-sm bg-accent hover:bg-accent disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-white transition-colors"
                    >
                        {isAnnotating ? (
                            <>
                                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                </svg>
                                Detecting...
                            </>
                        ) : (
                            <>
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
                                </svg>
                                Detect Features
                            </>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
