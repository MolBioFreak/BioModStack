export const PROTEIN_LOCAL_VALIDATORS = ['boltz2', 'esmfold2', 'protenix_v2'] as const;

export type ProteinLocalValidator = (typeof PROTEIN_LOCAL_VALIDATORS)[number];

const validatorSet = new Set<string>(PROTEIN_LOCAL_VALIDATORS);

export function normalizeProteinLocalValidators(
    params: Record<string, unknown> | null | undefined,
): ProteinLocalValidator[] {
    const raw = params?.structure_validators;
    let selected: unknown;
    if (raw === undefined) {
        selected = params?.run_boltz_validation === true ? ['boltz2'] : ['protenix_v2'];
    } else {
        selected = raw;
    }
    if (!Array.isArray(selected) || selected.length < 1 || selected.length > 3) {
        throw new Error('structure_validators must select between one and three validators');
    }
    if (selected.some((value) => typeof value !== 'string' || !validatorSet.has(value))) {
        throw new Error('structure_validators contains an unsupported validator');
    }
    if (new Set(selected).size !== selected.length) {
        throw new Error('structure_validators contains duplicates');
    }
    return PROTEIN_LOCAL_VALIDATORS.filter((validator) => selected.includes(validator));
}

export function toggleProteinLocalValidator(
    selected: readonly ProteinLocalValidator[],
    validator: ProteinLocalValidator,
): ProteinLocalValidator[] {
    const next = new Set(selected);
    if (next.has(validator)) {
        if (next.size === 1) return [...selected];
        next.delete(validator);
    } else {
        next.add(validator);
    }
    return PROTEIN_LOCAL_VALIDATORS.filter((candidate) => next.has(candidate));
}
