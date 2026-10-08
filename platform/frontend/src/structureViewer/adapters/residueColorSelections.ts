export interface ResidueColor {
    readonly r: number;
    readonly g: number;
    readonly b: number;
}

export interface LabelResidueColorSelection {
    readonly struct_asym_id: string;
    readonly residue_number: number;
    readonly color: ResidueColor;
}

export interface RejectedResidueColor {
    readonly key: string;
    readonly reason: 'invalidKey' | 'invalidColor';
}

export interface AdaptedResidueColors {
    readonly selections: readonly LabelResidueColorSelection[];
    readonly rejected: readonly RejectedResidueColor[];
}

const isValidColor = (color: ResidueColor): boolean => (
    [color.r, color.g, color.b].every((value) => Number.isInteger(value) && value >= 0 && value <= 255)
);

const parseResidueColorKey = (key: string): { chainId: string; residueNumber: number } | null => {
    if (!key || key !== key.trim()) return null;

    const match = key.includes(':')
        ? /^([^:]+):(-?\d+)$/.exec(key)
        : /^(.+?)(-?\d+)$/.exec(key);
    if (!match) return null;

    const chainId = match[1];
    const residueNumber = Number(match[2]);
    if (!chainId || !Number.isSafeInteger(residueNumber)) return null;
    return { chainId, residueNumber };
};

export function adaptLegacyResidueColors(
    residueColors: ReadonlyMap<string, ResidueColor>,
): AdaptedResidueColors {
    const selections: LabelResidueColorSelection[] = [];
    const rejected: RejectedResidueColor[] = [];

    for (const [key, color] of residueColors) {
        const identity = parseResidueColorKey(key);
        if (!identity) {
            rejected.push({ key, reason: 'invalidKey' });
            continue;
        }
        if (!isValidColor(color)) {
            rejected.push({ key, reason: 'invalidColor' });
            continue;
        }
        selections.push({
            struct_asym_id: identity.chainId,
            residue_number: identity.residueNumber,
            color,
        });
    }

    selections.sort((left, right) => (
        left.struct_asym_id.localeCompare(right.struct_asym_id)
        || left.residue_number - right.residue_number
    ));
    rejected.sort((left, right) => left.key.localeCompare(right.key));
    return { selections, rejected };
}
