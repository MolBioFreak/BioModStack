export type DiscoRow = Record<string, any>;
/** Parse only the ordered document shape; native science stays native-owned. */
export function parseDiscoJobs(text: string): DiscoRow[] {
    const jobs: unknown = JSON.parse(text);
    if (!Array.isArray(jobs) || jobs.some(job => !job || typeof job !== 'object' || Array.isArray(job)))
        throw new Error('Expected a native ordered array of DISCO job records');
    return jobs;
}
export const discoEntityKinds = ['proteinChain', 'dnaSequence', 'rnaSequence', 'ligand', 'ion'] as const;
export const discoEntityLabels = { proteinChain: 'Protein sequence / masks', dnaSequence: 'DNA strand (5′ → 3′)', rnaSequence: 'RNA strand (5′ → 3′)', ligand: 'Ligand / cofactor', ion: 'Ion' };
export interface DiscoProteinModification { ptmPosition: number; ptmType: string }
export interface DiscoNucleicModification { basePosition: number; modificationType: string }
/** Native dialects share no position conversion: both are one-based CCD records. */
export function discoModificationKeys(kind: typeof discoEntityKinds[number]) {
    return kind === 'proteinChain' ? ['ptmPosition', 'ptmType'] as const : ['basePosition', 'modificationType'] as const;
}
/** Reorder entities and their one-based bond references together, never alter strands. */
export function moveDiscoEntity(job: DiscoRow, from: number, to: number): DiscoRow {
    const order = job.sequences.map((_: unknown, i: number) => i) as number[];
    order.splice(to, 0, order.splice(from, 1)[0]);
    return { ...job, sequences: order.map(i => job.sequences[i]), ...(Array.isArray(job.covalent_bonds) ? {
        covalent_bonds: job.covalent_bonds.map((bond: DiscoRow) => {
            const next = { ...bond };
            for (const key of ['left_entity', 'right_entity']) if (Number.isInteger(bond[key]) && bond[key] >= 1 && bond[key] <= order.length) next[key] = order.indexOf(bond[key] - 1) + 1;
            return next;
        }),
    } : {}) };
}
export function removeDiscoEntity(job: DiscoRow, index: number): DiscoRow {
    return { ...job, sequences: job.sequences.filter((_: unknown, i: number) => i !== index), ...(Array.isArray(job.covalent_bonds) ? {
        covalent_bonds: job.covalent_bonds.filter((bond: DiscoRow) => bond.left_entity !== index + 1 && bond.right_entity !== index + 1).map((bond: DiscoRow) => {
            const next = { ...bond };
            for (const key of ['left_entity', 'right_entity']) if (Number.isInteger(bond[key]) && bond[key] > index + 1 && bond[key] <= job.sequences.length) next[key] = bond[key] - 1;
            return next;
        }),
    } : {}) };
}
export function discoInputAuthority(jobs: unknown, path: string): string {
    return jobs !== undefined && jobs !== null ? 'Explicit ordered jobs' : path ? 'Native JSON file' : 'Task shorthand';
}
