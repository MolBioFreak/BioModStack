/** Qualified against NVIDIA-Digital-Bio/la-proteina cde5de3ead6e4d76f367da6dc5174be9913ef6ca,
 * proteinfoundation/utils/motif_utils.py:119-181, 277-349, 598-620.
 * motif_only consumes whole chains once, in contig first-appearance order, ignoring
 * residue bounds. Therefore upload a real motif-only derivative, never the source.
 * This adapter changes neither atom selection nor scientific defaults.
 */
export function materializeLaProteinaMotifSelection(
    pdb: string, selected: ReadonlySet<string>, contig: string,
): { pdb: string; contig: string } {
    const residues = new Map<string, string[]>();
    if ((pdb.match(/^MODEL /gm) || []).length > 1) throw new Error('Choose one source model before making a motif.');
    for (const line of pdb.split(/\r?\n/)) {
        if (!line.startsWith('ATOM  ')) continue; // Native excludes hetero atoms.
        const key = `${line.slice(21, 22)}${Number(line.slice(22, 26))}${line.slice(26, 27).trim()}`;
        if (!residues.has(key)) residues.set(key, []);
        residues.get(key)!.push(line);
    }
    if (!selected.size) throw new Error('Select motif residues in the structure or residue grid.');
    const ordered: string[] = [];
    const seenChains = new Set<string>();
    let lastChain = '';
    for (const token of contig.split('/')) {
        if (/^\d+(?:-\d+)?$/.test(token)) {
            const [lo, hi = lo] = token.split('-').map(Number);
            if (hi < lo) throw new Error('Scaffold length ranges must be ascending.');
            continue;
        }
        const match = /^([ABCDEFGHJKLMNOPQRSTUVWXYZ])(\d+)(?:-(\d+))?$/.exec(token);
        if (!match) throw new Error('Enter explicit ordered blocks and scaffold lengths, e.g. 10/A25-27/5/B40/10. Native ranges cannot represent insertion codes or chain I. Manual native inputs remain available.');
        const [, chain, start, end = start] = match;
        if (Number(end) < Number(start)) throw new Error('Motif ranges must be ascending.');
        if (!Number.isSafeInteger(Number(start)) || !Number.isSafeInteger(Number(end)) || Number(end) - Number(start) + 1 > selected.size) throw new Error('Motif blocks must match the selected source residues.');
        if (chain !== lastChain && seenChains.has(chain)) throw new Error('Native motif-only parsing groups each chain once. Keep each chain’s blocks together; use manual native inputs for a separately prepared motif.');
        seenChains.add(chain); lastChain = chain;
        for (let n = Number(start); n <= Number(end); n++) {
            const key = `${chain}${n}`;
            if (!selected.has(key) || !residues.has(key)) throw new Error(`Block residue ${key} must be selected and have source ATOM records.`);
            if (ordered.includes(key)) throw new Error(`Motif residue ${key} occurs more than once.`);
            ordered.push(key);
        }
    }
    if (ordered.length !== selected.size) throw new Error('Ordered blocks must include every selected residue exactly once. Insertion-coded residues require separately prepared manual native inputs.');
    return { pdb: [...ordered.flatMap(key => residues.get(key)!), 'END', ''].join('\n'), contig };
}
