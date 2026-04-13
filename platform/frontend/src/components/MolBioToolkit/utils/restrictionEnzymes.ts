export type RestrictionEnzymeCategory =
    | 'common'
    | 'golden_gate'
    | 'rare'
    | 'additional'
    | 'nicking';

export type NickingStrand = 'top' | 'bottom';

export interface RestrictionEnzymeDefinition {
    name: string;
    site: string;
    category: RestrictionEnzymeCategory;
    viewerSupported?: boolean;
    nickingStrand?: NickingStrand;
    tags?: string[];
}

const IUPAC_BASES: Record<string, string> = {
    A: 'A',
    C: 'C',
    G: 'G',
    T: 'T',
    R: 'AG',
    Y: 'CT',
    S: 'GC',
    W: 'AT',
    K: 'GT',
    M: 'AC',
    B: 'CGT',
    D: 'AGT',
    H: 'ACT',
    V: 'ACG',
    N: 'ACGT',
};

const IUPAC_COMPLEMENT: Record<string, string> = {
    A: 'T',
    C: 'G',
    G: 'C',
    T: 'A',
    R: 'Y',
    Y: 'R',
    S: 'S',
    W: 'W',
    K: 'M',
    M: 'K',
    B: 'V',
    D: 'H',
    H: 'D',
    V: 'B',
    N: 'N',
};

function withCategory(
    category: RestrictionEnzymeCategory,
    enzymes: Array<Omit<RestrictionEnzymeDefinition, 'category'>>,
): RestrictionEnzymeDefinition[] {
    return enzymes.map((enzyme) => ({
        viewerSupported: enzyme.viewerSupported ?? true,
        ...enzyme,
        category,
    }));
}

export const RESTRICTION_ENZYME_GROUPS: Record<RestrictionEnzymeCategory, RestrictionEnzymeDefinition[]> = {
    common: withCategory('common', [
        { name: 'EcoRI', site: 'GAATTC' },
        { name: 'BamHI', site: 'GGATCC' },
        { name: 'HindIII', site: 'AAGCTT' },
        { name: 'XbaI', site: 'TCTAGA' },
        { name: 'SalI', site: 'GTCGAC' },
        { name: 'PstI', site: 'CTGCAG' },
        { name: 'NotI', site: 'GCGGCCGC' },
        { name: 'XhoI', site: 'CTCGAG' },
        { name: 'NcoI', site: 'CCATGG' },
        { name: 'NdeI', site: 'CATATG' },
        { name: 'BglII', site: 'AGATCT' },
        { name: 'SpeI', site: 'ACTAGT' },
        { name: 'KpnI', site: 'GGTACC' },
        { name: 'SacI', site: 'GAGCTC' },
        { name: 'ApaI', site: 'GGGCCC' },
        { name: 'SmaI', site: 'CCCGGG' },
        { name: 'MluI', site: 'ACGCGT' },
        { name: 'ClaI', site: 'ATCGAT' },
        { name: 'EcoRV', site: 'GATATC' },
        { name: 'NheI', site: 'GCTAGC' },
    ]),
    golden_gate: withCategory('golden_gate', [
        { name: 'BsaI', site: 'GGTCTC', tags: ['type_iis'] },
        { name: 'BbsI', site: 'GAAGAC', tags: ['type_iis'] },
        { name: 'SapI', site: 'GCTCTTC', tags: ['type_iis'] },
        { name: 'BsmBI', site: 'CGTCTC', tags: ['type_iis'] },
        { name: 'AarI', site: 'CACCTGC', tags: ['type_iis'] },
    ]),
    rare: withCategory('rare', [
        { name: 'AgeI', site: 'ACCGGT' },
        { name: 'AscI', site: 'GGCGCGCC' },
        { name: 'PacI', site: 'TTAATTAA' },
        { name: 'SfiI', site: 'GGCCNNNNNGGCC' },
        { name: 'FseI', site: 'GGCCGGCC' },
        { name: 'PmeI', site: 'GTTTAAAC' },
        { name: 'SwaI', site: 'ATTTAAAT' },
        { name: 'SgrAI', site: 'CRCCGGYG' },
    ]),
    additional: withCategory('additional', [
        { name: 'AatII', site: 'GACGTC' },
        { name: 'AccI', site: 'GTMKAC' },
        { name: 'AflII', site: 'CTTAAG' },
        { name: 'AflIII', site: 'ACRYGT' },
        { name: 'AluI', site: 'AGCT' },
        { name: 'AseI', site: 'ATTAAT' },
        { name: 'AvaI', site: 'CYCGRG' },
        { name: 'AvrII', site: 'CCTAGG' },
        { name: 'BanI', site: 'GGYRCC' },
        { name: 'BanII', site: 'GRGCYC' },
        { name: 'BclI', site: 'TGATCA' },
        { name: 'BlpI', site: 'GCTNAGC' },
        { name: 'BstXI', site: 'CCANNNNNNTGG' },
        { name: 'DpnI', site: 'GATC' },
        { name: 'DraI', site: 'TTTAAA' },
        { name: 'EagI', site: 'CGGCCG' },
        { name: 'FokI', site: 'GGATG', tags: ['type_iis'] },
        { name: 'HaeIII', site: 'GGCC' },
        { name: 'HhaI', site: 'GCGC' },
        { name: 'HincII', site: 'GTYRAC' },
        { name: 'HinfI', site: 'GANTC' },
        { name: 'HpaI', site: 'GTTAAC' },
        { name: 'HpaII', site: 'CCGG' },
        { name: 'MboI', site: 'GATC' },
        { name: 'MfeI', site: 'CAATTG' },
        { name: 'MscI', site: 'TGGCCA' },
        { name: 'MseI', site: 'TTAA' },
        { name: 'MspI', site: 'CCGG' },
        { name: 'NaeI', site: 'GCCGGC' },
        { name: 'NarI', site: 'GGCGCC' },
        { name: 'NciI', site: 'CCSGG' },
        { name: 'NlaIII', site: 'CATG' },
        { name: 'NruI', site: 'TCGCGA' },
        { name: 'NsiI', site: 'ATGCAT' },
        { name: 'PciI', site: 'ACATGT' },
        { name: 'PvuI', site: 'CGATCG' },
        { name: 'PvuII', site: 'CAGCTG' },
        { name: 'RsaI', site: 'GTAC' },
        { name: 'SacII', site: 'CCGCGG' },
        { name: 'ScaI', site: 'AGTACT' },
        { name: 'SphI', site: 'GCATGC' },
        { name: 'SspI', site: 'AATATT' },
        { name: 'StuI', site: 'AGGCCT' },
        { name: 'TaqI', site: 'TCGA' },
        { name: 'XcmI', site: 'CCANNNNNNNNNTGG' },
        { name: 'XmaI', site: 'CCCGGG' },
        { name: 'XmnI', site: 'GAANNNNTTC' },
        { name: 'ZraI', site: 'GACGTC' },
    ]),
    nicking: withCategory('nicking', [
        {
            name: 'Nt.BbvCI',
            site: 'CCTCAGC',
            viewerSupported: false,
            nickingStrand: 'top',
            tags: ['nicking'],
        },
        {
            name: 'Nb.BbvCI',
            site: 'CCTCAGC',
            viewerSupported: false,
            nickingStrand: 'bottom',
            tags: ['nicking'],
        },
        {
            name: 'Nt.BspQI',
            site: 'GCTCTTC',
            viewerSupported: false,
            nickingStrand: 'top',
            tags: ['nicking', 'type_iis'],
        },
        {
            name: 'Nb.BssSI',
            site: 'CACGAG',
            viewerSupported: false,
            nickingStrand: 'bottom',
            tags: ['nicking'],
        },
    ]),
};

export const ALL_RESTRICTION_ENZYMES = Object.values(RESTRICTION_ENZYME_GROUPS).flat();

export const RESTRICTION_ENZYME_INDEX = new Map(
    ALL_RESTRICTION_ENZYMES.map((enzyme) => [enzyme.name, enzyme]),
);

export function getRestrictionEnzyme(name: string): RestrictionEnzymeDefinition | undefined {
    return RESTRICTION_ENZYME_INDEX.get(name);
}

export function reverseComplementSite(site: string): string {
    return site
        .toUpperCase()
        .replace(/U/g, 'T')
        .split('')
        .reverse()
        .map((base) => IUPAC_COMPLEMENT[base] || base)
        .join('');
}

function baseMatches(sequenceBase: string, patternBase: string): boolean {
    const allowed = IUPAC_BASES[patternBase] || patternBase;
    return allowed.includes(sequenceBase);
}

export function findRestrictionSites(sequence: string, site: string, circular: boolean): number[] {
    const upperSeq = sequence.toUpperCase().replace(/U/g, 'T');
    const pattern = site.toUpperCase().replace(/U/g, 'T');
    if (!upperSeq || !pattern || pattern.length > upperSeq.length) {
        return [];
    }

    const patterns = Array.from(new Set([pattern, reverseComplementSite(pattern)]));
    const searchSpace = circular
        ? upperSeq + upperSeq.slice(0, pattern.length - 1)
        : upperSeq;
    const searchLimit = circular
        ? upperSeq.length
        : upperSeq.length - pattern.length + 1;

    const positions = new Set<number>();
    for (const candidate of patterns) {
        for (let start = 0; start < searchLimit; start += 1) {
            let matches = true;
            for (let offset = 0; offset < candidate.length; offset += 1) {
                if (!baseMatches(searchSpace[start + offset], candidate[offset])) {
                    matches = false;
                    break;
                }
            }
            if (matches) {
                positions.add(start);
            }
        }
    }

    return Array.from(positions).sort((a, b) => a - b);
}
