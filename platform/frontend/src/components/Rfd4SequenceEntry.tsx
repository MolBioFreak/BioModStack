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

function sequenceStats(text: string, molecule: Molecule) {
    const clean = text
        .split(/\r?\n/)
        .filter(line => !line.trimStart().startsWith('>'))
        .join('')
        .replace(/\s+/g, '');
    if (!clean.length) return '';
    if (molecule === 'protein') return `${clean.length} aa`;
    const gc = (clean.match(/[GCgc]/g) ?? []).length;
    const pct = Math.round((gc / clean.length) * 100);
    return `${clean.length} nt · ${pct}% GC`;
}

const moleculePillClass: Record<Molecule, string> = {
    protein: 'border-sky-500/35 bg-sky-500/10 text-sky-300',
    dna: 'border-emerald-500/35 bg-emerald-500/10 text-emerald-300',
    rna: 'border-amber-500/35 bg-amber-500/10 text-amber-300',
};

const moleculeLabel: Record<Molecule, string> = {
    protein: 'Protein',
    dna: 'DNA',
    rna: 'RNA',
};

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
    const stats = sequenceStats(text, molecule);
    return <section aria-label="Sequence input" className="space-y-3.5">
        <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]/85 p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-[var(--border-primary)]/60 pb-2.5">
                <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-[var(--text-primary)]">Sequence input</h3>
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${moleculePillClass[molecule]}`}>{moleculeLabel[molecule]}</span>
                    {stats && <span className="font-mono text-[11px] text-[var(--text-secondary)]">{stats}</span>}
                    {summary && <span role="status" className="rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-medium text-sky-300">{summary}</span>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <label className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">Molecule
                        <select
                            className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2.5 py-1 text-xs font-medium text-[var(--text-primary)] focus:border-[var(--accent-primary)] focus:outline-none"
                            value={molecule}
                            onChange={e => edit({ molecule: e.currentTarget.value as Molecule, text })}
                        >
                            <option value="protein">Protein</option>
                            <option value="dna">DNA</option>
                            <option value="rna">RNA</option>
                        </select>
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">Import FASTA
                        <input
                            className="max-w-[200px] rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-0.5 text-[11px] text-[var(--text-primary)] file:mr-2 file:rounded-md file:border-0 file:bg-[var(--bg-tertiary)] file:px-2 file:py-0.5 file:text-[11px] file:font-medium file:text-[var(--text-primary)] hover:file:bg-[var(--bg-secondary)]"
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
            </div>
            <label className="block space-y-1.5 text-xs font-medium text-[var(--text-secondary)]">
                <span className="flex items-center justify-between">
                    <span>Paste sequence</span>
                    {molecule !== 'protein' && <span className="font-mono text-[11px] text-[var(--text-secondary)]/80">5′ → 3′</span>}
                </span>
                <textarea
                    className="block w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2.5 font-mono text-xs tracking-wider text-[var(--text-primary)] placeholder:text-[var(--text-secondary)]/55 focus:border-[var(--accent-primary)] focus:outline-none"
                    rows={3}
                    spellCheck={false}
                    placeholder={molecule === 'protein' ? 'Paste a protein sequence or FASTA record…' : `Paste a 5′→3′ ${moleculeLabel[molecule]} sequence or FASTA record…`}
                    value={text}
                    onChange={e => edit({ molecule, text: e.currentTarget.value })}
                />
            </label>
            {error && <p role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
            {!summary && <p className="sr-only" role="status">{summary}</p>}
            <p className="text-[11px] text-[var(--text-secondary)]/80">Sequence input does not attach coordinates. For a structure-based task, choose a prepared structure explicitly.</p>
        </div>
        {molecule !== 'protein' && children}
    </section>;
}
