const inputClass = 'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 outline-none transition focus:border-cyan-500 disabled:cursor-not-allowed disabled:opacity-65';
const labelClass = 'block text-xs font-semibold uppercase tracking-[0.12em] text-slate-400';

export function Gen2WorkflowControl({
    label, value, onChange, min, max, step = 1, unit, description, fixed = false, slider = false, setting,
}: {
    label: string;
    value: number;
    onChange: (value: number) => void;
    min?: number;
    max?: number;
    step?: number;
    unit?: string;
    description?: string;
    fixed?: boolean;
    slider?: boolean;
    setting?: string;
}) {
    const number = <input className={inputClass} type="number" value={value} min={min} max={max} step={step} disabled={fixed} data-md-setting={setting} onChange={(event) => onChange(Number(event.target.value))} />;
    return (
        <label className={labelClass}>
            <span className="flex items-center justify-between gap-2"><span>{label}{unit ? ` (${unit})` : ''}</span>{fixed && <span className="normal-case tracking-normal text-cyan-300">Fixed by profile</span>}</span>
            {slider && min !== undefined && max !== undefined ? <div className="grid grid-cols-[minmax(0,1fr)_7rem] items-center gap-3"><input className="mt-2 w-full accent-cyan-400" type="range" value={value} min={min} max={max} step={step} disabled={fixed} aria-label={`${label} slider`} onChange={(event) => onChange(Number(event.target.value))} />{number}</div> : number}
            {description && <span className="mt-1 block normal-case tracking-normal text-[11px] font-normal text-slate-500">{description}</span>}
        </label>
    );
}
