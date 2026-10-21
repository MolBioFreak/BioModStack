export type ProtenixModification = { ptmPosition: number; ptmType: string } | { basePosition: number; modificationType: string };
const entityIdentity = Symbol('Protenix editor entity identity');
export const protenixEntityKey = (component: ProtenixComponent): string => component[entityIdentity] ?? component.id;
export const identifyProtenixComponent = <T extends ProtenixComponent>(component: T, key: string): T => ({ ...component, [entityIdentity]: key });
export function transportProtenixComponent(component: ProtenixComponent): ProtenixComponent {
    const result = { ...component }; delete result[entityIdentity]; return result;
}
export interface ProtenixComponent {
    [entityIdentity]?: string;
    id: string;
    type: 'protein' | 'peptide' | 'dna' | 'rna' | 'ligand' | 'ion';
    name?: string;
    sequence?: string;
    count?: number;
    copy_ids?: string[];
    modifications?: ProtenixModification[];
    ccd?: string;
    smiles?: string;
    ligand_file?: string;
    ion?: string;
    element?: string;
    pairedMsa?: string | null;
    unpairedMsa?: string | null;
    pairedMsaPath?: string | null;
    unpairedMsaPath?: string | null;
    msa_path?: string;
    msa?: { precomputed_msa_dir?: string };
    templatesPath?: string;
}
export interface ProtenixBond {
    left_entity?: number; right_entity?: number;
    left_position?: number; right_position?: number;
    left_atom?: string | number; right_atom?: string | number;
    left_copy?: number; right_copy?: number;
    entity1?: number; entity2?: number; position1?: number; position2?: number;
    atom1?: string | number; atom2?: string | number; copy1?: number; copy2?: number;
}
export interface ProtenixContact {
    entity1?: number; entity2?: number; copy1?: number; copy2?: number;
    position1?: number; position2?: number; atom1?: string | number; atom2?: string | number;
    max_distance?: number; min_distance?: number;
}
export interface ProtenixConstraint {
    contact?: ProtenixContact[];
    pocket?: { binder_chain: { entity?: number; copy?: number }; contact_residues: { entity?: number; copy?: number; position?: number }[]; max_distance?: number };
}

// Alignment invalidation owns alignment fields only, never chemical annotations.
export function updateProtenixComponent(original: ProtenixComponent, patch: Partial<ProtenixComponent>): ProtenixComponent {
    const next = { ...original, ...patch };
    for (const key of Object.keys(patch) as (keyof ProtenixComponent)[]) {
        if (patch[key] === undefined) delete next[key];
    }
    if (patch.sequence !== undefined && patch.sequence !== original.sequence) {
        for (const key of ['pairedMsa', 'unpairedMsa', 'pairedMsaPath', 'unpairedMsaPath', 'msa_path', 'msa'] as const) delete next[key];
    }
    return next;
}

// Rebase every supplied endpoint alias, never let a removed entity alias its successor.
// Zero is an intentionally unresolved ordinal and remains so until explicitly edited.
export function remapProtenixEndpoints(previous: string[], next: string[], bonds?: ProtenixBond[], constraint?: ProtenixConstraint) {
    const ordinal = (value: number | undefined) => {
        if (value === undefined) return value;
        const id = previous[value - 1];
        return id && previous.filter(x => x === id).length === 1 && next.filter(x => x === id).length === 1 ? next.indexOf(id) + 1 : 0;
    };
    const rebase = <T extends object>(record: T, keys: string[]): T => {
        const result = { ...record } as Record<string, unknown>;
        for (const key of keys) if (key in result) result[key] = ordinal(result[key] as number | undefined);
        return result as T;
    };
    return {
        bonds: bonds?.map(bond => rebase(bond, ['left_entity', 'right_entity', 'entity1', 'entity2'])),
        constraint: constraint && { ...constraint,
            ...(constraint.contact ? { contact: constraint.contact.map(contact => rebase(contact, ['entity1', 'entity2'])) } : {}),
            ...(constraint.pocket ? { pocket: { ...constraint.pocket,
                binder_chain: rebase(constraint.pocket.binder_chain, ['entity']),
                contact_residues: constraint.pocket.contact_residues.map(residue => rebase(residue, ['entity'])),
            } } : {}),
        },
    };
}

export function protenixEndpointErrors(ids: string[], bonds?: ProtenixBond[], constraint?: ProtenixConstraint): string[] {
    const errors: string[] = [];
    if (new Set(ids).size !== ids.length) errors.push('Duplicate component IDs: assign a unique ID to each entity.');
    const check = (record: object, keys: string[], label: string) => {
        for (const key of keys) if (key in record) {
            const value = (record as Record<string, unknown>)[key];
            if (!Number.isInteger(value) || Number(value) < 1 || Number(value) > ids.length)
                errors.push(`${label} ${key}: unresolved entity endpoint. Update the endpoint or remove the affected bond/constraint.`);
        }
    };
    bonds?.forEach((bond, i) => check(bond, ['left_entity', 'right_entity', 'entity1', 'entity2'], `Bond ${i + 1}`));
    constraint?.contact?.forEach((contact, i) => check(contact, ['entity1', 'entity2'], `Contact ${i + 1}`));
    if (constraint?.pocket) {
        check(constraint.pocket.binder_chain, ['entity'], 'Pocket binder');
        constraint.pocket.contact_residues.forEach((residue, i) => check(residue, ['entity'], `Pocket residue ${i + 1}`));
    }
    return errors;
}

// Preserve entity ordering: native bond endpoints are ordinals, not BMS labels.
export function orderedProtenixComponents(originalIds: string[], primary: ProtenixComponent, additional: ProtenixComponent[]): ProtenixComponent[] {
    const components = [primary, ...additional];
    return components.sort((a, b) => {
        const rank = (id: string) => originalIds.includes(id) ? originalIds.indexOf(id) : originalIds.length;
        return rank(protenixEntityKey(a)) - rank(protenixEntityKey(b));
    });
}
