import { useEffect, useState, type ReactNode } from 'react';
import { DeNovoDocumentEditorPanels, StudioTaskRail } from './DeNovoDocumentEditors';

export type Rfd4EntryProps = {
    visit?: number; task: string; hasTask: boolean; sourceFree: boolean; applied: string[]; inspected: string[];
    sequence: ReactNode; start: (recipe: string) => void;
};
export function Rfd4EntryWorkspace({ entry, picker, children }: { entry: Rfd4EntryProps; picker: (choose: () => void) => ReactNode; children: ReactNode }) {
    const [rail, setRail] = useState<HTMLDivElement | null>(null);
    const [view, setView] = useState<'start' | 'task' | 'sequence'>(entry.hasTask ? 'task' : 'start');
    useEffect(() => { if (entry.visit) setView('start'); }, [entry.visit]);
    const choices = [['New chains', 'Specify the chains to generate. No input file.', 'source_free'], ['Open a structure', 'Choose a CIF or PDB from files or RCSB.', 'protein_context'], ['Start from a sequence', 'Paste or import sequence; choose protein, DNA or RNA.', 'sequence']] as const;
    const pane = 'min-w-0 space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3.5';
    return <div className="grid min-w-0 items-start gap-3.5 lg:grid-cols-[minmax(0,7fr)_minmax(260px,3fr)]">
        <section aria-label="AtomWorks entry workspace" className={pane}>
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-primary)] pb-2"><h3 className="text-sm font-semibold">AtomWorks Studio</h3>
                {view !== 'start' && <button type="button" className="rfd4-btn-sm" onClick={() => setView('start')}>Starting points</button>}
            </header>
            {view === 'start' && <div className="space-y-2.5"><h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">Start a design</h4><div className="grid gap-2.5 sm:grid-cols-3">{choices.map(([label, description, target]) => <button key={target} className="rfd4-start-card block w-full text-left" type="button" aria-label={label} onClick={() => {
                if (target === 'sequence') setView('sequence'); else { entry.start(target); setView('task'); }
            }}><strong className="block text-sm">{label}</strong><span className="mt-1 block text-xs text-[var(--text-secondary)]">{description}</span></button>)}</div></div>}
            {view === 'sequence' && entry.sequence}
            <div hidden={view !== 'task'} className="space-y-3"><StudioTaskRail.Provider value={rail}><DeNovoDocumentEditorPanels /></StudioTaskRail.Provider>{children}</div>
        </section>
        <aside aria-label="Active task" className={`${pane} lg:sticky lg:top-4`}>
            <h3 className="border-b border-[var(--border-primary)] pb-2 text-sm font-semibold">Active task</h3>
            <div className="space-y-1 text-xs">
                <p className="font-medium text-[var(--text-primary)]">{entry.hasTask ? entry.task : 'No task selected'}</p>
                <p className="text-[var(--text-secondary)]">Task input: {entry.applied.length ? entry.applied.join(', ') : entry.sourceFree ? 'Not required' : 'No structure attached'}</p>
            </div>
            <div ref={setRail} />
            {!!entry.inspected.length && <p className="text-xs text-[var(--text-secondary)]">Inspected: {entry.inspected.join(', ')}</p>}
            {view === 'sequence' && <p className="text-xs text-[var(--text-secondary)]">Sequence is not coordinates. Choose a prepared structure explicitly to use it as a task input.</p>}
            {entry.hasTask && view !== 'task' && <button type="button" className="rfd4-btn-sm w-full" onClick={() => setView('task')}>Return to task</button>}
            <div className="pt-1">{picker(() => setView('task'))}</div>
        </aside>
    </div>;
}
