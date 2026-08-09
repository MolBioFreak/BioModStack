import {
    FRUSTRAMPNN_MULTI_TARGET_LIMIT,
    type FrustraMpnnResultReference,
} from '../../lib/frustraMpnnApi.js';

export const MAX_FRUSTRAMPNN_MULTI_TARGETS = FRUSTRAMPNN_MULTI_TARGET_LIMIT;

export interface FrustraMpnnSelectableResult extends FrustraMpnnResultReference {
    label: string;
}

export const frustraMpnnResultReferenceKey = (reference: FrustraMpnnResultReference): string => (
    `${reference.parent_job_id}\u0000${reference.invocation_id}`
);

const sameReference = (left: FrustraMpnnResultReference, right: FrustraMpnnResultReference): boolean => (
    left.parent_job_id === right.parent_job_id && left.invocation_id === right.invocation_id
);

export const appendFrustraMpnnComparisonTarget = (
    selected: readonly FrustraMpnnSelectableResult[],
    candidate: FrustraMpnnSelectableResult,
    reference: FrustraMpnnResultReference,
): FrustraMpnnSelectableResult[] => {
    if (sameReference(candidate, reference)) {
        throw new Error('The reference result cannot also be selected as a target.');
    }
    if (selected.some((item) => sameReference(item, candidate))) return [...selected];
    if (selected.length >= MAX_FRUSTRAMPNN_MULTI_TARGETS) return [...selected];
    return [...selected, candidate];
};

export const moveFrustraMpnnComparisonTarget = (
    selected: readonly FrustraMpnnSelectableResult[],
    index: number,
    direction: -1 | 1,
): FrustraMpnnSelectableResult[] => {
    const destination = index + direction;
    if (index < 0 || index >= selected.length || destination < 0 || destination >= selected.length) {
        return [...selected];
    }
    const moved = [...selected];
    [moved[index], moved[destination]] = [moved[destination]!, moved[index]!];
    return moved;
};

export const removeFrustraMpnnComparisonTarget = (
    selected: readonly FrustraMpnnSelectableResult[],
    index: number,
): FrustraMpnnSelectableResult[] => selected.filter((_item, itemIndex) => itemIndex !== index);
