export type Molecule = 'protein' | 'dna' | 'rna';

export function fastaRoster(text: string, molecule: Molecule) {
    const rows: {row_id:string;header:string;sequence:string}[] = [];
    for (const line of text.split(/\r?\n/)) {
        if (line.startsWith('>')) rows.push({row_id:`row_${String(rows.length+1).padStart(8,'0')}`,header:line.slice(1),sequence:''});
        else if (line.trim()) { if (!rows.length) throw new Error('Sequence before FASTA header'); rows[rows.length-1].sequence += line.replace(/\s/g,''); }
    }
    return {molecule, rows, source_format:'fasta', source_text:text};
}
