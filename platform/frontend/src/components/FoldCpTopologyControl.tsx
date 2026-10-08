export const foldCpTopologyMeaning = '2D (default) uses a square context-parallel mesh. 1D uses a single context-parallel axis and permits non-square CP sizes. CP size must divide the selected GPU count (1–16).';
export const foldCpConfidenceCaveat = 'Upstream cautions that 1D confidence values can diverge at CP > 1. Execution and outputs remain available; interpret confidence with care.';

export function FoldCpTopologyControl({ value, onChange, disabled = false }: {
    value?: '2d' | '1d'; onChange: (value: '2d' | '1d') => void; disabled?: boolean;
}) {
    return <div>
        <label className="block text-sm text-orange-100/80 mb-1">Context Parallel Topology
            <select aria-label="Context Parallel Topology" value={value ?? '2d'} disabled={disabled}
                onChange={event => onChange(event.target.value as '2d' | '1d')}
                className="block w-full max-w-xs bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm">
                <option value="2d">2D — square mesh (default)</option>
                <option value="1d">1D — single axis</option>
            </select>
        </label>
        <p className="text-xs text-slate-400">{foldCpTopologyMeaning}</p>
        {(value ?? '2d') === '1d' && <p role="note" className="mt-2 text-xs text-amber-200">{foldCpConfidenceCaveat}</p>}
    </div>;
}
