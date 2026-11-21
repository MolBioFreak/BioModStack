import type { ReactNode } from 'react';

export type BioXpChoice = { value: string; label: ReactNode; detail?: ReactNode; lead?: ReactNode; disabled?: boolean; title?: string };

/** One selected value shown as a row of buttons instead of a dropdown, so every choice is visible. */
export function BioXpChoiceList({ label, value, options, onChange, onCommit, className = '' }: {
    label: string; value: string; options: BioXpChoice[]; onChange: (value: string) => void;
    /** Double-click on a choice: select and act in one gesture. */
    onCommit?: (value: string) => void; className?: string;
}) {
    return <div role="listbox" aria-label={label} data-value={value} className={`bxc ${className}`}>
        {options.map(option => <button key={option.value} type="button" role="option" aria-selected={option.value === value} data-value={option.value}
            disabled={option.disabled} title={option.title} onClick={() => onChange(option.value)} onDoubleClick={onCommit ? () => onCommit(option.value) : undefined}>
            {option.lead}<span className="bxc-label">{option.label}</span>{option.detail != null && <small>{option.detail}</small>}</button>)}
    </div>;
}
