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
    return <section aria-label="Sequence input" className="space-y-3">
        <h3 className="font-semibold">Sequence input</h3>
        <div className="flex flex-wrap items-end gap-3">
            <label>Molecule <select value={molecule} onChange={e => edit({ molecule: e.currentTarget.value as Molecule, text })}>
                <option value="protein">Protein</option><option value="dna">DNA</option><option value="rna">RNA</option>
            </select></label>
            <label>Import FASTA<input className="block max-w-full text-sm" type="file" accept=".fa,.fasta,.faa,.fna,.ffn,.frn" onChange={e => {
                const file = e.currentTarget.files?.[0];
                e.currentTarget.value = '';
                if (file) void importFile(file);
            }} /></label>
        </div>
        <label className="block">Paste sequence<textarea className="block w-full rounded border p-2 font-mono" rows={4} spellCheck={false}
            placeholder="Paste a sequence or FASTA record…" value={text} onChange={e => edit({ molecule, text: e.currentTarget.value })} /></label>
        {error && <p role="alert">{error}</p>}
        <p className="text-sm" role="status">{recordSummary(text, molecule)}</p>
        <p className="text-sm">Sequence input does not attach coordinates. For a structure-based task, choose a prepared structure explicitly.</p>
        {molecule !== 'protein' && children}
    </section>;
}
