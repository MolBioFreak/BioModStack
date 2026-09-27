import type { CSSProperties, ReactNode } from 'react';

// The existing RFD3 workflow presentation, shared without its scientific state.
export const themedPanelStyle: CSSProperties = {
    backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border-primary)', color: 'var(--text-primary)',
};
export const themedInsetStyle: CSSProperties = {
    backgroundColor: 'color-mix(in srgb, var(--bg-tertiary) 58%, transparent)', borderColor: 'var(--border-primary)', color: 'var(--text-primary)',
};
export const themedMutedInsetStyle: CSSProperties = {
    backgroundColor: 'color-mix(in srgb, var(--bg-tertiary) 42%, transparent)', borderColor: 'var(--border-primary)', color: 'var(--text-secondary)',
};
export const themedSelectedStyle = (accent: string): CSSProperties => ({
    backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`,
    borderColor: `color-mix(in srgb, ${accent} 72%, var(--border-primary))`, color: 'var(--text-primary)',
});
export const themedInputStyle: CSSProperties = {
    backgroundColor: 'var(--bg-tertiary)', borderColor: 'var(--border-primary)', color: 'var(--text-primary)', caretColor: 'var(--accent-primary)',
};

export function ProteinDesignSections({ label, sections, active, onChange }: {
    label: string; sections: readonly string[]; active: string; onChange: (section: string) => void;
}) {
    return <nav aria-label={label} className="flex flex-wrap gap-2" data-bms-protein-design-sections>
        {sections.map(item => <button key={item} type="button" aria-pressed={active === item}
            onClick={() => onChange(item)} className="rounded-lg border px-3 py-2 text-sm"
            style={active === item ? themedSelectedStyle('var(--accent-primary)') : themedInsetStyle}>{item}</button>)}
    </nav>;
}

export function ProteinDesignPanel({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
    return <section aria-label={title} className="min-w-0 space-y-4 rounded-xl border p-4" style={themedPanelStyle}>
        <header><h2 className="text-lg font-semibold">{title}</h2>
            {description && <div className="mt-1 text-sm text-[var(--text-secondary)]">{description}</div>}
        </header>
        {children}
    </section>;
}

export function ProteinDesignRun({ jobName, onJobNameChange, children, pending, disabled = false, onSubmit, submitLabel, onCancel }: {
    jobName: string; onJobNameChange: (name: string) => void; children: ReactNode;
    pending: boolean; disabled?: boolean; onSubmit: () => void; submitLabel: string; onCancel?: () => void;
}) {
    return <section aria-label="Run" className="space-y-4" data-bms-protein-design-run>
        <div className="rounded-lg border p-3" style={themedInsetStyle}>
            <label className="block text-sm font-medium">Job name
                <input aria-label="Job name" value={jobName} onChange={event => onJobNameChange(event.target.value)}
                    className="mt-2 w-full rounded-lg border px-3 py-2 text-sm outline-none" style={themedInputStyle} />
            </label>
        </div>
        {children}
        <div className="flex justify-end gap-3">
            {onCancel && <button type="button" onClick={onCancel} className="rounded-lg border px-5 py-3 text-sm font-medium" style={themedInsetStyle}>Cancel</button>}
            <button type="button" onClick={onSubmit} disabled={pending || disabled}
                className="rounded-lg border px-5 py-3 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                style={themedSelectedStyle('var(--accent-primary)')}>{pending ? 'Submitting…' : submitLabel}</button>
        </div>
    </section>;
}
