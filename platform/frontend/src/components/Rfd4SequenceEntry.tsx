import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { fastaRoster, type Molecule } from '../lib/fastaRoster';

export interface Rfd4SequenceDraft {
    molecule: Molecule;
    text: string;
}

function recordSummary(text: string, molecule: Molecule) {
    if (!/^>/m.test(text)) return '';
    try {
        const count = fastaRoster(text, molecule).rows.length;
        return `${count} FASTA record${count === 1 ? '' : 's'}`;
    } catch {
        return 'FASTA records must begin with a header line (>).';
    }
}

export function Rfd4SequenceEntry({ value, onChange, children }: {
    value?: Rfd4SequenceDraft;
    onChange: (draft: Rfd4SequenceDraft) => void;
    children?: ReactNode;
}) {
    const { molecule = 'protein', text = '' } = value ?? {};
    const generation = useRef(0);
    const [error, setError] = useState('');
    useLayoutEffect(() => () => { generation.current++; }, [molecule, text]);
    function edit(next: Rfd4SequenceDraft) {
        generation.current++;
        setError('');
        onChange(next);
    }
    async function importFile(file: File) {
        const ticket = ++generation.current;
        setError('');
        try {
            const raw = await file.text();
            if (ticket !== generation.current) return;
            const roster = fastaRoster(raw, molecule);
            if (!roster.rows.length) throw new Error('No FASTA records');
            edit({ molecule, text: raw });
        } catch {
            if (ticket === generation.current) setError('Could not import FASTA. Choose a readable file with a header line (>) before the sequence.');
        }
    }
    const summary = recordSummary(text, molecule);
    return <section aria-label="Sequence input" className="space-y-3">
        <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-3.5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-primary)]/70 pb-2">
                <div>
                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Sequence input</h3>
                    <p className="text-[11px] text-[var(--text-secondary)]">Sequence input does not attach coordinates. For a structure-based task, choose a prepared structure explicitly.</p>
                </div>
                {summary && <span role="status" className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-medium text-sky-300">{summary}</span>}
            </div>
            <div className="grid gap-3 sm:grid-cols-[minmax(140px,180px)_minmax(0,1fr)] items-end">
                <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)]">Molecule
                    <select
                        className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] focus:border-[var(--accent-primary)] focus:outline-none"
                        value={molecule}
                        onChange={e => edit({ molecule: e.currentTarget.value as Molecule, text })}
                    >
                        <option value="protein">Protein</option>
                        <option value="dna">DNA</option>
                        <option value="rna">RNA</option>
                    </select>
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)]">Import FASTA
                    <input
                        className="block w-full max-w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2.5 py-1 text-xs text-[var(--text-primary)] file:mr-2.5 file:rounded-md file:border-0 file:bg-[var(--bg-secondary)] file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-[var(--text-primary)] hover:file:bg-[var(--bg-primary)]"
                        type="file"
                        accept=".fa,.fasta,.faa,.fna,.ffn,.frn"
                        onChange={e => {
                            const file = e.currentTarget.files?.[0];
                            e.currentTarget.value = '';
                            if (file) void importFile(file);
                        }}
                    />
                </label>
            </div>
            <label className="block space-y-1 text-xs font-medium text-[var(--text-secondary)]">Paste sequence
                <textarea
                    className="block w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2.5 font-mono text-xs text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]/60 focus:border-[var(--accent-primary)] focus:outline-none"
                    rows={4}
                    spellCheck={false}
                    placeholder="Paste a sequence or FASTA record…"
                    value={text}
                    onChange={e => edit({ molecule, text: e.currentTarget.value })}
                />
            </label>
            {error && <p role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
            {!summary && <p className="sr-only" role="status">{summary}</p>}
        </div>
        {molecule !== 'protein' && children}
    </section>;
}
