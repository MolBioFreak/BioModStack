export interface LaMotifAtom { residue: string; atom: string; altloc: string; }
/** Read literal ATOM identities. Do not manufacture absent sidechains or merge MODELs. */
export function laMotifAtoms(pdb: string, residues: ReadonlySet<string>): LaMotifAtom[] {
    const result: LaMotifAtom[] = [];
    for (const line of pdb.split(/\r?\n/)) {
        if (!line.startsWith('ATOM  ')) continue;
        const residue = `${line.slice(21, 22)}${Number(line.slice(22, 26))}${line.slice(26, 27).trim()}`;
        if (residues.has(residue)) result.push({ residue, atom: line.slice(12, 16).trim(), altloc: line.slice(16, 17).trim() });
    }
    return result;
}
export const laAtomKey = (atom: LaMotifAtom) => JSON.stringify([atom.residue, atom.atom, atom.altloc]);
/** Native motif_atom_spec addresses atom names, not alternate conformers. */
export function laMotifAtomSpec(atoms: LaMotifAtom[], selected: ReadonlySet<string>): string {
    const groups = new Map<string, Set<string>>();
    for (const atom of atoms) if (selected.has(laAtomKey(atom))) {
        if (!groups.has(atom.residue)) groups.set(atom.residue, new Set());
        groups.get(atom.residue)!.add(atom.atom);
    }
    return [...groups].map(([residue, names]) => `${residue}: [${[...names].join(', ')}]`).join('; ');
}
