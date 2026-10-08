/**
 * VisibilityPanel - Toggle annotation layer visibility
 */

interface VisibilityState {
    features: boolean;
    primers: boolean;
    cutsites: boolean;
    translations: boolean;
    reverseComplement: boolean;
}

interface VisibilityPanelProps {
    visibility: VisibilityState;
    onChange: (key: keyof VisibilityState) => void;
    className?: string;
}

const VISIBILITY_LABELS: Record<keyof VisibilityState, string> = {
    features: 'Features',
    primers: 'Primers',
    cutsites: 'Restriction Sites',
    translations: 'Translations',
    reverseComplement: 'Complement Strand'
};

export function VisibilityPanel({ visibility, onChange, className }: VisibilityPanelProps) {
    return (
        <div className={`visibility-panel p-3 space-y-2 ${className || ''}`}>
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
    );
}
