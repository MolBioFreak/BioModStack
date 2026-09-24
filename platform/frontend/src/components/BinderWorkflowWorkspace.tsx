import type { ReactNode } from 'react';

export interface BinderWorkspaceSection { id: string; label: string }
export interface BinderWorkflowWorkspaceProps {
    title: string;
    description?: ReactNode;
    sections: readonly BinderWorkspaceSection[];
    activeSection: string;
    onSectionChange: (section: string) => void;
    engineChooser?: ReactNode;
    children: ReactNode;
    summary?: ReactNode;
    executionControls?: ReactNode;
    submitControls?: ReactNode;
    library?: ReactNode;
}

/** Presentation only: native owners retain drafts, validation and submission. */
export function BinderWorkflowWorkspace({ title, description, sections, activeSection, onSectionChange,
    engineChooser, children, summary, executionControls, submitControls, library }: BinderWorkflowWorkspaceProps) {
    return <section aria-label={title} className="space-y-5 rounded-xl border p-4 sm:p-6"
        style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border-primary)', color: 'var(--text-primary)' }}>
        <header><h2 className="text-lg font-semibold">{title}</h2>{description && <div className="mt-1 text-sm text-[var(--text-secondary)]">{description}</div>}</header>
        {engineChooser && <details className="rounded-lg border p-3" style={{ borderColor: 'var(--border-primary)' }}>
            <summary className="cursor-pointer font-medium">Change generation engine</summary>
            <div className="mt-3">{engineChooser}</div>
        </details>}
        <nav aria-label="Binder workspace sections" className="flex flex-wrap gap-2">
            {sections.map(section => <button key={section.id} type="button" aria-current={activeSection === section.id ? 'page' : undefined}
                className="rounded-lg border px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2"
                style={{ borderColor: activeSection === section.id ? 'var(--accent-primary)' : 'var(--border-primary)', background: activeSection === section.id ? 'var(--bg-tertiary)' : 'transparent' }}
                onClick={() => onSectionChange(section.id)}>{section.label}</button>)}
        </nav>
        <div className="min-w-0">{children}</div>
        {summary && <aside aria-label="Request summary" className="text-sm text-[var(--text-secondary)]">{summary}</aside>}
        {executionControls}
        {submitControls && <footer>{submitControls}</footer>}
        {library}
    </section>;
}
