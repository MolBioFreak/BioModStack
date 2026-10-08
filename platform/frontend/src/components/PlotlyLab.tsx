import { useState, type ReactNode } from 'react';

export interface MetricPickerProps {
    label: string; keys: string[]; value: string; onChange: (value: string) => void; allowEmpty?: boolean;
}
export interface PlotlyLabAxes {
    x: string; y: string; z?: string; color: string;
    onX: (value: string) => void; onY: (value: string) => void;
    onZ?: (value: string) => void; onColor: (value: string) => void;
}
const control = 'min-w-0 w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2 text-sm text-[var(--text-primary)]';

/** Shared controlled workbench extracted from AnalyticsDashboard's Custom Plotly Lab.
 * Consumers retain their own data authority, trace builders, selection and axis state.
 */
export function PlotlyLab({ keys, getMetricLabel, renderMetricPicker, axes2d, axes3d, plot2d, plot3d,
    dimension: controlledDimension, onDimensionChange, palette, onPalette, reverse, onReverse, palettes = ['Viridis', 'Plasma', 'Cividis', 'Turbo', 'Inferno', 'Magma', 'Electric', 'Portland', 'Bluered', 'RdBu'],
}: {
    keys: string[]; getMetricLabel: (key: string) => string;
    renderMetricPicker?: (props: MetricPickerProps) => ReactNode;
    axes2d: PlotlyLabAxes; axes3d: PlotlyLabAxes; plot2d: ReactNode; plot3d: ReactNode;
    palette: string; onPalette: (value: string) => void; reverse: boolean; onReverse: (value: boolean) => void;
    palettes?: string[]; dimension?: '2D' | '3D'; onDimensionChange?: (value: '2D' | '3D') => void;
}) {
    const [localDimension, setLocalDimension] = useState<'2D' | '3D'>('2D');
    const dimension = controlledDimension ?? localDimension;
    const setDimension = (value: '2D' | '3D') => { setLocalDimension(value); onDimensionChange?.(value); };
    const axes = dimension === '2D' ? axes2d : axes3d;
    const picker = (label: string, value: string, onChange: (value: string) => void, allowEmpty = false) => {
        const props = { label, keys, value, onChange, allowEmpty };
        return renderMetricPicker ? renderMetricPicker(props) : <label className="flex min-w-0 flex-col gap-1 text-xs">{label}
            <select aria-label={label} className={control} value={value} onChange={event => onChange(event.target.value)}>
                {allowEmpty && <option value="">No color metric</option>}
                {keys.map(key => <option key={key} value={key} title={key}>{getMetricLabel(key)}</option>)}
            </select>
        </label>;
    };
    return <section aria-label="Plotly Lab" className="min-w-0 space-y-3 rounded-lg border border-[var(--border-color)] p-3">
        <h3 className="font-semibold">Plotly Lab</h3>
        <p className="text-xs text-[var(--text-secondary)]">Choose axes and color to explore recorded measurements. Each point is one record with values on every chosen axis. Camera exports the plot; double-click resets zoom.</p>
        <div className="flex flex-wrap items-center gap-3">
            <label className="text-xs">Plot view<select aria-label="Plotly Lab view" className={control} value={dimension} onChange={event => setDimension(event.target.value as '2D' | '3D')}><option>2D</option><option>3D</option></select></label>
            <label className="text-xs">Palette<select aria-label="Plotly Lab palette" className={control} value={palette} onChange={event => onPalette(event.target.value)}>{palettes.map(value => <option key={value}>{value}</option>)}</select></label>
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={reverse} onChange={event => onReverse(event.target.checked)} />Reverse palette</label>
        </div>
        <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {picker(`${dimension} X metric`, axes.x, axes.onX)}
            {picker(`${dimension} Y metric`, axes.y, axes.onY)}
            {dimension === '3D' && axes.onZ && picker('3D Z metric', axes.z ?? '', axes.onZ)}
            {picker(`${dimension} color metric`, axes.color, axes.onColor, true)}
        </div>
        {dimension === '2D' ? plot2d : plot3d}
    </section>;
}
