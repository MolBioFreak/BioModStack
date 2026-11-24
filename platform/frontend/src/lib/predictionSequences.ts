import { fastaRoster } from './fastaRoster';

type Entry = { name: string; sequence: string };

/** Primary plain text is one wrapped sequence; plain batch text is one record per line. */
export function predictionSequences(raw: string, mode: 'primary' | 'batch', prefix = 'variant'): { entries: Entry[]; error?: string } {
    const text = raw.trim();
    if (!text) return { entries: [] };
    try {
        let rows: Entry[];
        if (/^>/m.test(text)) {
            rows = fastaRoster(text, 'protein').rows.map(({ header, sequence }, index) => {
                if (!header.trim()) throw new Error(`FASTA record ${index + 1} needs a name.`);
                return { name: header.trim(), sequence };
            });
            if (mode === 'primary' && rows.length !== 1) throw new Error('Enter one primary record; use Sequence Matrix Batch for multiple records.');
        } else if (mode === 'primary') {
            rows = [{ name: '', sequence: text }];
        } else {
            rows = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((line, index) => {
                const named = /^([^:]+?):\s*(.*)$/.exec(line) ?? /^(\S+)\s+(.+)$/.exec(line);
                return { name: named?.[1].trim() || `${prefix.trim() || 'variant'}_${String(index + 1).padStart(3, '0')}`, sequence: named ? named[2] : line };
            });
        }
        const entries = rows.map(({ name, sequence }, index) => {
            const residues = sequence.replace(/\s/g, '');
            if (!/^[A-Za-z]+$/.test(residues)) throw new Error(`Record ${index + 1}${name ? ` (${name})` : ''} needs a nonempty sequence of letters A–Z.`);
            return { name, sequence: residues.toUpperCase() };
        });
        return { entries };
    } catch (error) {
        return { entries: [], error: error instanceof Error ? error.message : 'Invalid sequence input.' };
    }
}
