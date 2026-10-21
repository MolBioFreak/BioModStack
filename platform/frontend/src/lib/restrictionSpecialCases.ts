// Operator-facing classification only: never a second restriction-analysis engine.
// Exact catalog IDs requiring modified CYTOSINES, not methylation-sensitive cutters
// or methyladenine-dependent DpnI/MalI. Native records/results remain authoritative.
// Sources, chemistry and whole-catalog reconciliation:
// docs/scientific-sources/restriction-modified-cytosine-special-cases.md
const METHYL_C = 'Requires methylated cytosines (5mC) in the appropriate sequence context.';
const MSPJI_FAMILY = 'Requires methylated or hydroxymethylated cytosines (5mC or 5hmC); glucosylation of 5hmC blocks recognition.';
const SPECIAL_CASES: Readonly<Record<string, string>> = {
    AbaSI: 'Requires hydroxymethylated cytosines (5hmC), with enhanced activity on beta-glucosylated 5hmC; alpha-glucosylation strongly inhibits activity relative to beta-glucosylation.',
    AbaUMB2I: METHYL_C,
    AoxI: METHYL_C,
    AspBHI: MSPJI_FAMILY,
    BisI: 'Requires methylated cytosines (5mC); activity depends on the number and arrangement of modified cytosines in the site.',
    BlsI: METHYL_C,
    BmeDI: 'Requires modified cytosines, with a preference for hydroxymethylated cytosines (5hmC). Glucosylated substrates require enzyme-specific interpretation.',
    Dde51507I: METHYL_C,
    EcoBLMcrX: 'Requires methylated or hydroxymethylated cytosines (5mC or 5hmC); modification density and sequence context affect cleavage.',
    EcoO157SI: 'Requires modified cytosines; recognizes hydroxymethylated cytosines (5hmC) and also cleaves methylated cytosines (5mC).',
    FspEI: MSPJI_FAMILY,
    GlaI: 'Requires methylated cytosines (5mC); activity depends on the number and arrangement of modified cytosines in the site.',
    GluI: METHYL_C,
    KroI: METHYL_C,
    LpnPI: MSPJI_FAMILY,
    MspJI: MSPJI_FAMILY,
    MteI: METHYL_C,
    NhoI: 'Requires modified cytosines (5mC or 5hmC); activity depends on modification density and sequence context.',
    PcsI: METHYL_C,
    Pfl8569I: METHYL_C,
    PkrI: METHYL_C,
    PsuGI: METHYL_C,
    RlaI: MSPJI_FAMILY,
    SgeI: METHYL_C,
    SgrTI: MSPJI_FAMILY,
    TagI: 'Requires methylated or hydroxymethylated cytosines (5mC or 5hmC), not glucosylated 5hmC; two modified sites enhance cleavage.',
    YkrI: 'Requires modified cytosines, with a preference for hydroxymethylated cytosines (5hmC). Glucosylated substrates require enzyme-specific interpretation.',
};

export function restrictionSpecialCase(enzymeId: string): string | undefined {
    if (!Object.hasOwn(SPECIAL_CASES, enzymeId)) return undefined;
    return `${SPECIAL_CASES[enzymeId]} Sequence matches alone do not establish cleavage; displayed sites are sequence motifs only.`;
}
