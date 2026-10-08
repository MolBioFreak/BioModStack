/**
 * VisibilityPanel - Toggle annotation layer visibility + settings
 */

import { COLOR_PALETTES, type ColorPaletteName } from './SequenceViewer';

interface VisibilityState {
    features: boolean;
    primers: boolean;
    cutsites: boolean;
    translations: boolean;
    reverseComplement: boolean;
}

// Reading frame configuration
export type ReadingFrame = 1 | 2 | 3 | -1 | -2 | -3;

interface VisibilityPanelProps {
    visibility: VisibilityState;
    onChange: (key: keyof VisibilityState) => void;
    colorPalette?: ColorPaletteName;
    onColorPaletteChange?: (palette: ColorPaletteName) => void;
    visibleFrames?: Set<ReadingFrame>;
    onVisibleFramesChange?: (frames: Set<ReadingFrame>) => void;
    className?: string;
}

const VISIBILITY_LABELS: Record<keyof VisibilityState, string> = {
    features: 'Features',
    primers: 'Primers',
    cutsites: 'Restriction Sites',
    translations: 'Translations',
    reverseComplement: 'Complement Strand'
};

const FRAME_OPTIONS: { value: ReadingFrame; label: string; color: string }[] = [
    { value: 1, label: '+1', color: '#22c55e' },
    { value: 2, label: '+2', color: '#3b82f6' },
    { value: 3, label: '+3', color: '#f59e0b' },
    { value: -1, label: '−1', color: '#ef4444' },
    { value: -2, label: '−2', color: '#8b5cf6' },
    { value: -3, label: '−3', color: '#ec4899' },
];

export function VisibilityPanel({
    visibility,
    onChange,
    colorPalette = 'classic',
    onColorPaletteChange,
    visibleFrames = new Set([1]),
    onVisibleFramesChange,
    className
}: VisibilityPanelProps) {

    const toggleFrame = (frame: ReadingFrame) => {
        if (!onVisibleFramesChange) return;
        const newFrames = new Set(visibleFrames);
        if (newFrames.has(frame)) {
            newFrames.delete(frame);
        } else {
            newFrames.add(frame);
        }
        onVisibleFramesChange(newFrames);
    };

    const selectAllFrames = () => {
        if (!onVisibleFramesChange) return;
        onVisibleFramesChange(new Set([1, 2, 3, -1, -2, -3]));
    };

    const clearAllFrames = () => {
        if (!onVisibleFramesChange) return;
        onVisibleFramesChange(new Set());
    };

    return (
        <div className={`visibility-panel p-3 space-y-4 ${className || ''}`}>
            {/* Layer Toggles */}
            <div className="space-y-2">
                <h4 className="font-semibold text-sm text-slate-300 mb-3">Show/Hide Layers</h4>
                {(Object.keys(visibility) as Array<keyof VisibilityState>).map((key) => (
                    <label
                        key={key}
                        className="flex items-center gap-2 text-sm cursor-pointer hover:bg-slate-700/50 px-2 py-1 rounded"
                    >
                        <input
                            type="checkbox"
                            checked={visibility[key]}
                            onChange={() => onChange(key)}
                            className="w-4 h-4 rounded border-slate-500 text-blue-500 focus:ring-blue-500 focus:ring-offset-slate-800"
                        />
                        <span className="text-slate-200">{VISIBILITY_LABELS[key]}</span>
                    </label>
                ))}
            </div>

            {/* Reading Frame Selector - only show when translations are enabled */}
            {visibility.translations && onVisibleFramesChange && (
                <div className="border-t border-slate-700 pt-3">
                    <div className="flex items-center justify-between mb-2">
                        <h4 className="font-semibold text-sm text-slate-300">Reading Frames</h4>
                        <div className="flex gap-1">
                            <button
                                onClick={selectAllFrames}
                                className="px-1.5 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 rounded"
                            >
                                All
                            </button>
                            <button
                                onClick={clearAllFrames}
                                className="px-1.5 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 rounded"
                            >
                                None
                            </button>
                        </div>
                    </div>
                    <div className="grid grid-cols-3 gap-1">
                        {FRAME_OPTIONS.map(({ value, label, color }) => (
                            <button
                                key={value}
                                onClick={() => toggleFrame(value)}
                                className={`px-2 py-1.5 text-xs rounded border transition-all ${visibleFrames.has(value)
                                        ? 'border-current font-semibold'
                                        : 'border-slate-600 bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                                style={visibleFrames.has(value) ? {
                                    backgroundColor: `${color}22`,
                                    borderColor: color,
                                    color
                                } : {}}
                            >
                                {label}
                            </button>
                        ))}
                    </div>
                    <p className="text-xs text-slate-500 mt-1.5">
                        {visibleFrames.size === 0
                            ? 'No frames selected'
                            : `${visibleFrames.size} frame${visibleFrames.size !== 1 ? 's' : ''} visible`}
                    </p>
                </div>
            )}

            {/* Color Palette Selector */}
            {onColorPaletteChange && (
                <div className="border-t border-slate-700 pt-3">
                    <h4 className="font-semibold text-sm text-slate-300 mb-2">Base Colors</h4>
                    <select
                        value={colorPalette}
                        onChange={(e) => onColorPaletteChange(e.target.value as ColorPaletteName)}
                        className="w-full px-2 py-1.5 bg-slate-700 border border-slate-600 rounded text-sm focus:outline-none focus:border-blue-500"
                    >
                        {(Object.keys(COLOR_PALETTES) as ColorPaletteName[]).map(key => (
                            <option key={key} value={key}>
                                {COLOR_PALETTES[key].name}
                            </option>
                        ))}
                    </select>
                    <p className="text-xs text-slate-500 mt-1">
                        {COLOR_PALETTES[colorPalette].description}
                    </p>

                    {/* Color preview */}
                    <div className="flex items-center gap-1 mt-2">
                        {(['A', 'T', 'G', 'C'] as const).map(base => (
                            <div
                                key={base}
                                className="flex-1 flex flex-col items-center"
                            >
                                <div
                                    className="w-6 h-6 rounded font-bold text-xs flex items-center justify-center text-black/60"
                                    style={{ backgroundColor: COLOR_PALETTES[colorPalette].colors[base] }}
                                >
                                    {base}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
