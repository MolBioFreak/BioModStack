// Explicit operator-facing exceptions, not a second restriction-analysis engine.
// Keep the native catalog/results intact; these enzymes require modified substrates.
const SPECIAL_CASES: Readonly<Record<string, string>> = {
    AbaSI: 'Requires hydroxymethylated or glucosyl-hydroxymethylated cytosines. No cleavage on unmodified DNA; displayed sites are sequence motifs only.',
    MspJI: 'Requires methylated or hydroxymethylated cytosines. No cleavage on unmodified DNA; displayed sites are sequence motifs only.',
};

// Primary substrate descriptions:
// https://www.neb.com/en/products/r0665-abasi
// https://www.neb.com/en/products/r0661-mspji
export function restrictionSpecialCase(enzymeId: string): string | undefined {
    return Object.hasOwn(SPECIAL_CASES, enzymeId) ? SPECIAL_CASES[enzymeId] : undefined;
}
