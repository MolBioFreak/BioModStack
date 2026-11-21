import { useState, type ReactNode } from 'react';
import { DeNovoDocumentEditorPanels, StudioTaskRail } from './DeNovoDocumentEditors';

export type Rfd4EntryProps = {
    task: string; hasTask: boolean; sourceFree: boolean; applied: string[]; inspected: string[];
    sequence: ReactNode; start: (recipe: string) => void;
};
export function Rfd4EntryWorkspace({ entry, picker, children }: { entry: Rfd4EntryProps; picker: (choose: () => void) => ReactNode; children: ReactNode }) {
    const [rail, setRail] = useState<HTMLDivElement | null>(null);
    const [view, setView] = useState<'start' | 'task' | 'sequence'>(entry.hasTask ? 'task' : 'start');
    const choices = [['New chains', 'Specify the chains to generate. No input file.', 'source_free'], ['Open a structure', 'Choose a CIF or PDB from files or RCSB.', 'protein_context'], ['Start from a sequence', 'Paste or import sequence; choose protein, DNA or RNA.', 'sequence']] as const;
    const pane = 'min-w-0 space-y-3 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-4';
    return <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(0,7fr)_minmax(0,3fr)]">
        <section aria-label="AtomWorks entry workspace" className={pane}>
            <header className="flex flex-wrap items-center justify-between border-b border-[var(--border-primary)] pb-2"><h3 className="font-semibold">AtomWorks Studio</h3>
                {view !== 'start' && <button type="button" onClick={() => setView('start')}>Starting points</button>}
            </header>
            {view === 'start' && <><h4>Start a design</h4>{choices.map(([label, description, target]) => <button key={target} className="block w-full" type="button" aria-label={label} onClick={() => {
                if (target === 'sequence') setView('sequence'); else { entry.start(target); setView('task'); }
            }}><strong className="block">{label}</strong><span className="text-sm">{description}</span></button>)}</>}
            {view === 'sequence' && entry.sequence}
            <div hidden={view !== 'task'} className="space-y-4"><StudioTaskRail.Provider value={rail}><DeNovoDocumentEditorPanels /></StudioTaskRail.Provider>{children}</div>
        </section>
        <aside aria-label="Active task" className={pane}>
            <h3 className="border-b border-[var(--border-primary)] pb-2 font-semibold">Active task</h3>
            <p>{entry.hasTask ? entry.task : 'No task selected'}</p>
            <p>Task input: {entry.applied.length ? entry.applied.join(', ') : entry.sourceFree ? 'Not required' : 'No structure attached'}</p>
            <div ref={setRail} />
            {!!entry.inspected.length && <p>Inspected: {entry.inspected.join(', ')}</p>}
            {view === 'sequence' && <p>Sequence is not coordinates. Choose a prepared structure explicitly to use it as a task input.</p>}
            {entry.hasTask && view !== 'task' && <button type="button" onClick={() => setView('task')}>Return to task</button>}
            {picker(() => setView('task'))}
        </aside>
    </div>;
}
