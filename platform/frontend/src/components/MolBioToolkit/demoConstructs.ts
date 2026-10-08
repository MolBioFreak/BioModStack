import type { Feature, Primer, SequenceData } from './types';
import { calculateGcPercent, reverseComplementSequence } from './utils/nucleotides';

type FeatureType = Feature['type'];

interface DemoFeatureSpec {
    name: string;
    type: FeatureType;
    start: number;
    length?: number;
    sequence?: string;
    strand?: 1 | -1;
    description?: string;
}

interface DemoPrimerTemplate {
    name: string;
    feature: string;
    side?: 'start' | 'end';
    length?: number;
    offset?: number;
    strand?: 1 | -1;
}

interface DemoConstructSpec {
    name: string;
    description: string;
    totalLength: number;
    seed: string;
    features: DemoFeatureSpec[];
    primers?: DemoPrimerTemplate[];
}

const FEATURE_COLORS: Record<string, string> = {
    CDS: '#22c55e',
    gene: '#3b82f6',
    promoter: '#8b5cf6',
    terminator: '#ef4444',
    rep_origin: '#ec4899',
    primer_bind: '#f59e0b',
    misc_feature: '#64748b',
};

const MCS_SEQUENCE = 'GAATTCGGTACCCGGGGATCCTCTAGAGTCGACCTGCAGGCATGCAAGCTT';
const HIS_TAG_SEQUENCE = 'CATCACCATCACCATCAC';
const P2A_SEQUENCE = 'GGAAGCGGAGCTACTAACTTCAGCCTGCTGAAGCAGGCTGGCGACGTGGAGGAGAACCCTGGACCT';
const FORBIDDEN_RESTRICTION_SITES = [
    'GAATTC', // EcoRI
    'GGATCC', // BamHI
    'AAGCTT', // HindIII
    'TCTAGA', // XbaI
    'GTCGAC', // SalI
    'CTGCAG', // PstI
    'GCGGCCGC', // NotI
    'CTCGAG', // XhoI
    'CCATGG', // NcoI
    'CATATG', // NdeI
    'AGATCT', // BglII
    'ACTAGT', // SpeI
    'GGTACC', // KpnI
    'GAGCTC', // SacI
    'GGGCCC', // ApaI
    'CCCGGG', // SmaI/XmaI
    'ACGCGT', // MluI
    'ATCGAT', // ClaI
    'GATATC', // EcoRV
    'GCTAGC', // NheI
    'GGTCTC', // BsaI
    'GAAGAC', // BbsI
    'GCTCTTC', // SapI
    'CGTCTC', // BsmBI
    'CACCTGC', // AarI
];
const SENSE_CODONS = [
    'GCT', 'GCC', 'GCA', 'GCG',
    'CGT', 'CGC', 'CGA', 'CGG', 'AGA', 'AGG',
    'AAT', 'AAC',
    'GAT', 'GAC',
    'TGT', 'TGC',
    'CAA', 'CAG',
    'GAA', 'GAG',
    'GGT', 'GGC', 'GGA', 'GGG',
    'CAT', 'CAC',
    'ATT', 'ATC', 'ATA',
    'CTT', 'CTC', 'CTA', 'CTG', 'TTA', 'TTG',
    'AAA', 'AAG',
    'ATG',
    'TTT', 'TTC',
    'CCT', 'CCC', 'CCA', 'CCG',
    'TCT', 'TCC', 'TCA', 'TCG', 'AGT', 'AGC',
    'ACT', 'ACC', 'ACA', 'ACG',
    'TGG',
    'TAT', 'TAC',
    'GTT', 'GTC', 'GTA', 'GTG',
];
const STOP_CODONS = ['TAA', 'TAG', 'TGA'];

function hashString(value: string): number {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
        hash ^= value.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
}

function generateBackbone(length: number, seed: string): string {
    let state = hashString(seed) || 1;
    const bases = ['A', 'T', 'G', 'C'];
    const chars = new Array<string>(length);
    for (let index = 0; index < length; index += 1) {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
        chars[index] = bases[state % bases.length];
    }
    return chars.join('');
}

function motifSequence(length: number, motif: string): string {
    if (length <= 0) return '';
    return motif.repeat(Math.ceil(length / motif.length)).slice(0, length).toUpperCase();
}

function nextSeededState(state: number): number {
    return (Math.imul(state, 1664525) + 1013904223) >>> 0;
}

function introducesForbiddenSite(sequence: string): boolean {
    return FORBIDDEN_RESTRICTION_SITES.some((site) => sequence.includes(site));
}

function pickSeededCodon(
    sequencePrefix: string,
    state: number,
    previousCodons: string[],
): { codon: string; state: number } {
    let localState = state;
    const tail = sequencePrefix.slice(-12);

    for (let attempt = 0; attempt < SENSE_CODONS.length; attempt += 1) {
        localState = nextSeededState(localState);
        const codonIndex = (localState + attempt) % SENSE_CODONS.length;
        const codon = SENSE_CODONS[codonIndex];
        const candidate = `${tail}${codon}`;
        const recentPair = previousCodons.slice(-1).join('');
        const repeatedTriplet = previousCodons.length >= 2
            && previousCodons[previousCodons.length - 1] === codon
            && previousCodons[previousCodons.length - 2] === codon;
        if (repeatedTriplet) {
            continue;
        }
        if (recentPair && candidate.endsWith(recentPair.repeat(2))) {
            continue;
        }
        if (introducesForbiddenSite(candidate)) {
            continue;
        }
        return { codon, state: localState };
    }

    localState = nextSeededState(localState);
    return {
        codon: SENSE_CODONS[localState % SENSE_CODONS.length],
        state: localState,
    };
}

function codingSequence(length: number, seed: string): string {
    const normalizedLength = Math.max(6, length - (length % 3));
    let state = hashString(seed) || 1;
    const chars = ['ATG'];
    const bodyCodons: string[] = [];
    const bodyLength = normalizedLength - 6;
    for (let offset = 0; offset < bodyLength; offset += 3) {
        const picked = pickSeededCodon(chars.join(''), state, bodyCodons);
        bodyCodons.push(picked.codon);
        chars.push(picked.codon);
        state = picked.state;
    }
    chars.push(STOP_CODONS[hashString(`${seed}-stop`) % STOP_CODONS.length]);
    return chars.join('');
}

function featureSequence(spec: DemoFeatureSpec, seed: string): string {
    if (spec.sequence) return spec.sequence.toUpperCase();
    if (!spec.length) {
        throw new Error(`Feature "${spec.name}" needs a length or explicit sequence`);
    }

    if (spec.type === 'CDS' || spec.type === 'gene') {
        return codingSequence(spec.length, seed);
    }
    if (spec.type === 'promoter') {
        return motifSequence(spec.length, 'TTGACATATAATAGGCGCGCC');
    }
    if (spec.type === 'terminator') {
        return motifSequence(spec.length, 'GCCGTTTTTTGCGCGCAAAAA');
    }
    if (spec.type === 'rep_origin') {
        return motifSequence(spec.length, 'ATCGGCGATCGATATCGCCGATTA');
    }
    return motifSequence(spec.length, 'GGTCTCGAATTCGCGGCCGCACTAGT');
}

function overlaySequence(base: string, insert: string, start: number): string {
    return `${base.slice(0, start)}${insert}${base.slice(start + insert.length)}`;
}

function estimateTm(sequence: string): number {
    const upper = sequence.toUpperCase();
    const a = (upper.match(/A/g) || []).length;
    const t = (upper.match(/T/g) || []).length;
    const g = (upper.match(/G/g) || []).length;
    const c = (upper.match(/C/g) || []).length;
    if (upper.length < 14) return 2 * (a + t) + 4 * (g + c);
    return Number((64.9 + (41 * (g + c - 16.4)) / upper.length).toFixed(1));
}

function buildConstruct(spec: DemoConstructSpec): SequenceData {
    let sequence = generateBackbone(spec.totalLength, spec.seed);
    const features: Feature[] = [];
    const featureLookup = new Map<string, Feature>();

    for (const [index, featureSpec] of spec.features.entries()) {
        const builtSequence = featureSequence(featureSpec, `${spec.seed}-${featureSpec.name}`);
        const start = featureSpec.start;
        const end = start + builtSequence.length;
        if (end > spec.totalLength) {
            throw new Error(`Feature "${featureSpec.name}" exceeds demo construct length for "${spec.name}"`);
        }
        sequence = overlaySequence(sequence, builtSequence, start);
        const feature: Feature = {
            id: `${spec.seed}-feature-${index + 1}`,
            name: featureSpec.name,
            type: featureSpec.type,
            start,
            end,
            strand: featureSpec.strand ?? 1,
            color: FEATURE_COLORS[featureSpec.type] || FEATURE_COLORS.misc_feature,
            description: featureSpec.description,
        };
        features.push(feature);
        featureLookup.set(feature.name, feature);
    }

    const primers: Primer[] = (spec.primers || []).map((template, index) => {
        const feature = featureLookup.get(template.feature);
        if (!feature) {
            throw new Error(`Primer "${template.name}" references missing feature "${template.feature}"`);
        }
        const primerLength = template.length ?? 24;
        const offset = template.offset ?? 0;
        const strand = template.strand ?? (template.side === 'end' ? -1 : 1);
        const start = template.side === 'end'
            ? feature.end - primerLength - offset
            : feature.start + offset;
        const end = start + primerLength;
        const window = sequence.slice(start, end);
        const primerSequence = strand === -1
            ? reverseComplementSequence(window, 'dna')
            : window;
        return {
            id: `${spec.seed}-primer-${index + 1}`,
            name: template.name,
            sequence: primerSequence,
            start,
            end,
            strand,
            tm: estimateTm(primerSequence),
            gc_percent: calculateGcPercent(primerSequence),
        };
    });

    return {
        name: spec.name,
        description: spec.description,
        sequence,
        circular: true,
        sequenceType: 'dna',
        features,
        primers,
        translations: [],
    };
}

const DEMO_SPECS: DemoConstructSpec[] = [
    {
        name: 'pUC19-Style Cloning Backbone',
        description: '2.95 kb cloning vector with lac promoter, MCS, lacZ alpha, AmpR, and dual bacterial origins.',
        totalLength: 2950,
        seed: 'puc19-demo',
        features: [
            { name: 'pMB1 ori', type: 'rep_origin', start: 140, length: 610 },
            { name: 'lac promoter', type: 'promoter', start: 860, length: 108 },
            { name: 'MCS', type: 'misc_feature', start: 1000, sequence: MCS_SEQUENCE },
            { name: 'lacZ alpha', type: 'CDS', start: 1085, length: 360 },
            { name: 'AmpR', type: 'CDS', start: 1840, length: 858, strand: -1 },
            { name: 'f1 ori', type: 'rep_origin', start: 2550, length: 240 },
        ],
        primers: [
            { name: 'M13/pUC F', feature: 'MCS', side: 'start', length: 24, strand: 1 },
            { name: 'AmpR R', feature: 'AmpR', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'Golden Gate L0 sfGFP Part',
        description: '3.18 kb MoClo-style entry plasmid with promoter, RBS, sfGFP payload, BsaI-flanked assembly region, and KanR.',
        totalLength: 3180,
        seed: 'goldengate-sfgfp-demo',
        features: [
            { name: 'BsaI left flank', type: 'misc_feature', start: 130, sequence: 'GGTCTCAAGGTCTC' },
            { name: 'J23101 promoter', type: 'promoter', start: 240, length: 70 },
            { name: 'RBS', type: 'misc_feature', start: 336, sequence: 'AGGAGGAAAAACAT' },
            { name: 'sfGFP CDS', type: 'CDS', start: 390, length: 720 },
            { name: 'B0015 terminator', type: 'terminator', start: 1180, length: 110 },
            { name: 'KanR', type: 'CDS', start: 1640, length: 816, strand: -1 },
            { name: 'p15A ori', type: 'rep_origin', start: 2470, length: 520 },
        ],
        primers: [
            { name: 'sfGFP F', feature: 'sfGFP CDS', side: 'start', length: 24, strand: 1 },
            { name: 'sfGFP R', feature: 'sfGFP CDS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'pET-28a-Style His Expression',
        description: '5.38 kb bacterial protein production vector with T7 promoter, 6xHis cassette, kinase insert, KanR, and lacI.',
        totalLength: 5380,
        seed: 'pet28-demo',
        features: [
            { name: 'ColE1 ori', type: 'rep_origin', start: 260, length: 650 },
            { name: 'KanR', type: 'CDS', start: 1020, length: 816, strand: -1 },
            { name: 'lacI', type: 'CDS', start: 2140, length: 1080, strand: -1 },
            { name: 'T7 promoter', type: 'promoter', start: 3440, sequence: 'TAATACGACTCACTATAGGG' },
            { name: '6xHis tag + MCS', type: 'misc_feature', start: 3490, sequence: `${HIS_TAG_SEQUENCE}${MCS_SEQUENCE}` },
            { name: 'Kinase insert', type: 'CDS', start: 3605, length: 1230 },
            { name: 'T7 terminator', type: 'terminator', start: 4920, length: 110 },
        ],
        primers: [
            { name: 'T7 F', feature: 'T7 promoter', side: 'start', length: 20, strand: 1 },
            { name: 'Insert R', feature: 'Kinase insert', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'pBAD-Style AraC Expression',
        description: '5.84 kb arabinose-inducible vector with pBAD promoter, secretion enzyme insert, araC regulator, and AmpR.',
        totalLength: 5840,
        seed: 'pbad-demo',
        features: [
            { name: 'pBR322 ori', type: 'rep_origin', start: 280, length: 780 },
            { name: 'AmpR', type: 'CDS', start: 1380, length: 858, strand: -1 },
            { name: 'araC', type: 'CDS', start: 2460, length: 879, strand: -1 },
            { name: 'pBAD promoter', type: 'promoter', start: 3540, length: 108 },
            { name: 'MCS', type: 'misc_feature', start: 3680, sequence: MCS_SEQUENCE },
            { name: 'Secreted enzyme', type: 'CDS', start: 3765, length: 900 },
            { name: 'rrnB T1 terminator', type: 'terminator', start: 4860, length: 105 },
        ],
        primers: [
            { name: 'pBAD F', feature: 'pBAD promoter', side: 'start', length: 24, strand: 1 },
            { name: 'Insert R', feature: 'Secreted enzyme', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'pcDNA3.1-Style CMV mCherry',
        description: '6.72 kb mammalian expression plasmid with CMV promoter, mCherry payload, NeoR cassette, SV40 elements, and AmpR.',
        totalLength: 6720,
        seed: 'pcdna3-demo',
        features: [
            { name: 'CMV promoter', type: 'promoter', start: 260, length: 620 },
            { name: 'mCherry CDS', type: 'CDS', start: 980, length: 711 },
            { name: 'BGH polyA', type: 'terminator', start: 1800, length: 225 },
            { name: 'NeoR', type: 'CDS', start: 2540, length: 795, strand: -1 },
            { name: 'SV40 promoter', type: 'promoter', start: 3440, length: 250 },
            { name: 'SV40 ori', type: 'rep_origin', start: 3860, length: 220 },
            { name: 'pUC ori', type: 'rep_origin', start: 4580, length: 620 },
            { name: 'AmpR', type: 'CDS', start: 5410, length: 858, strand: -1 },
        ],
        primers: [
            { name: 'CMV F', feature: 'CMV promoter', side: 'start', length: 24, strand: 1 },
            { name: 'mCherry R', feature: 'mCherry CDS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'Lentiviral EF1a GFP Puro Transfer',
        description: '9.42 kb lentiviral transfer plasmid with LTRs, packaging signals, EF1a-eGFP-Puro cassette, WPRE, AmpR, and ColE1 origin.',
        totalLength: 9420,
        seed: 'lenti-transfer-demo',
        features: [
            { name: "5' LTR", type: 'misc_feature', start: 120, length: 300 },
            { name: 'Psi packaging signal', type: 'misc_feature', start: 500, length: 180 },
            { name: 'RRE', type: 'misc_feature', start: 860, length: 300 },
            { name: 'cPPT/CTS', type: 'misc_feature', start: 1320, length: 240 },
            { name: 'EF1a promoter', type: 'promoter', start: 1820, length: 420 },
            { name: 'eGFP CDS', type: 'CDS', start: 2320, length: 720 },
            { name: 'P2A linker', type: 'misc_feature', start: 3055, sequence: P2A_SEQUENCE },
            { name: 'PuroR', type: 'CDS', start: 3145, length: 603 },
            { name: 'WPRE', type: 'misc_feature', start: 3840, length: 600 },
            { name: "3' LTR", type: 'misc_feature', start: 4560, length: 300 },
            { name: 'AmpR', type: 'CDS', start: 5850, length: 858, strand: -1 },
            { name: 'ColE1 ori', type: 'rep_origin', start: 7120, length: 650 },
        ],
        primers: [
            { name: 'EF1a F', feature: 'EF1a promoter', side: 'start', length: 24, strand: 1 },
            { name: 'eGFP R', feature: 'eGFP CDS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'AAV CAG EGFP Transfer',
        description: '4.58 kb AAV transfer plasmid with flanking ITRs, CAG promoter, EGFP payload, polyA signal, AmpR, and compact bacterial origin.',
        totalLength: 4580,
        seed: 'aav-cag-egfp-demo',
        features: [
            { name: "5' ITR", type: 'misc_feature', start: 40, length: 145 },
            { name: 'CAG promoter', type: 'promoter', start: 240, length: 680 },
            { name: 'Synthetic intron', type: 'misc_feature', start: 950, length: 160 },
            { name: 'EGFP CDS', type: 'CDS', start: 1140, length: 720 },
            { name: 'BGH polyA', type: 'terminator', start: 1940, length: 220 },
            { name: "3' ITR", type: 'misc_feature', start: 2310, length: 145 },
            { name: 'AmpR', type: 'CDS', start: 3000, length: 858, strand: -1 },
            { name: 'pUC ori', type: 'rep_origin', start: 3920, length: 520 },
        ],
        primers: [
            { name: 'CAG F', feature: 'CAG promoter', side: 'start', length: 24, strand: 1 },
            { name: 'EGFP R', feature: 'EGFP CDS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'scAAV hSyn GCaMP Sensor',
        description: '4.98 kb self-complementary AAV-style sensor plasmid with hSyn promoter, GCaMP cargo, WPRE3, ITRs, AmpR, and pUC origin.',
        totalLength: 4980,
        seed: 'scaav-gcamp-demo',
        features: [
            { name: "5' ITR", type: 'misc_feature', start: 35, length: 145 },
            { name: 'hSyn promoter', type: 'promoter', start: 230, length: 470 },
            { name: 'GCaMP sensor CDS', type: 'CDS', start: 780, length: 1350 },
            { name: 'WPRE3', type: 'misc_feature', start: 2230, length: 320 },
            { name: 'hGH polyA', type: 'terminator', start: 2610, length: 180 },
            { name: "3' ITR", type: 'misc_feature', start: 2920, length: 145 },
            { name: 'AmpR', type: 'CDS', start: 3180, length: 858, strand: -1 },
            { name: 'pUC ori', type: 'rep_origin', start: 4090, length: 520 },
        ],
        primers: [
            { name: 'hSyn F', feature: 'hSyn promoter', side: 'start', length: 24, strand: 1 },
            { name: 'GCaMP R', feature: 'GCaMP sensor CDS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'CRISPR Cas9 sgRNA Puro',
        description: '11.84 kb all-in-one CRISPR plasmid with U6 sgRNA cassette, CBh-SpCas9, PuroR, WPRE, AmpR, and pUC origin.',
        totalLength: 11840,
        seed: 'crispr-cas9-demo',
        features: [
            { name: 'U6 promoter', type: 'promoter', start: 220, length: 260 },
            { name: 'sgRNA scaffold', type: 'misc_feature', start: 520, length: 110 },
            { name: 'CBh promoter', type: 'promoter', start: 920, length: 420 },
            { name: 'SpCas9-NLS', type: 'CDS', start: 1440, length: 4107 },
            { name: 'P2A linker', type: 'misc_feature', start: 5615, sequence: P2A_SEQUENCE },
            { name: 'PuroR', type: 'CDS', start: 5705, length: 603 },
            { name: 'WPRE', type: 'misc_feature', start: 6520, length: 600 },
            { name: 'AmpR', type: 'CDS', start: 8500, length: 858, strand: -1 },
            { name: 'pUC ori', type: 'rep_origin', start: 9720, length: 620 },
        ],
        primers: [
            { name: 'sgRNA F', feature: 'sgRNA scaffold', side: 'start', length: 22, strand: 1 },
            { name: 'Cas9 R', feature: 'SpCas9-NLS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'PiggyBac EF1a CAR Donor',
        description: '9.68 kb transposon donor plasmid with PiggyBac arms, EF1a-driven CAR payload, mCherry reporter, WPRE, AmpR, and pUC origin.',
        totalLength: 9680,
        seed: 'piggybac-demo',
        features: [
            { name: 'PB left arm', type: 'misc_feature', start: 40, length: 240 },
            { name: 'EF1a promoter', type: 'promoter', start: 520, length: 420 },
            { name: 'CAR payload', type: 'CDS', start: 1000, length: 2130 },
            { name: 'P2A linker', type: 'misc_feature', start: 3200, sequence: P2A_SEQUENCE },
            { name: 'mCherry reporter', type: 'CDS', start: 3290, length: 711 },
            { name: 'WPRE', type: 'misc_feature', start: 4130, length: 600 },
            { name: 'PB right arm', type: 'misc_feature', start: 4860, length: 240 },
            { name: 'AmpR', type: 'CDS', start: 6200, length: 858, strand: -1 },
            { name: 'pUC ori', type: 'rep_origin', start: 7420, length: 620 },
        ],
        primers: [
            { name: 'CAR F', feature: 'CAR payload', side: 'start', length: 24, strand: 1 },
            { name: 'mCherry R', feature: 'mCherry reporter', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'pYES2 GAL1 Secretion Vector',
        description: '6.12 kb yeast shuttle plasmid with 2-micron origin, URA3, GAL1 promoter, secretion signal, enzyme payload, AmpR, and pUC origin.',
        totalLength: 6120,
        seed: 'pyes2-demo',
        features: [
            { name: '2-micron ori', type: 'rep_origin', start: 250, length: 620 },
            { name: 'URA3', type: 'CDS', start: 980, length: 804, strand: -1 },
            { name: 'GAL1 promoter', type: 'promoter', start: 1980, length: 320 },
            { name: 'Alpha-factor signal', type: 'CDS', start: 2350, length: 255 },
            { name: 'Secreted enzyme', type: 'CDS', start: 2630, length: 960 },
            { name: 'CYC1 terminator', type: 'terminator', start: 3690, length: 240 },
            { name: 'AmpR', type: 'CDS', start: 4410, length: 858, strand: -1 },
            { name: 'pUC ori', type: 'rep_origin', start: 5310, length: 520 },
        ],
        primers: [
            { name: 'GAL1 F', feature: 'GAL1 promoter', side: 'start', length: 24, strand: 1 },
            { name: 'Enzyme R', feature: 'Secreted enzyme', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'pCAMBIA 35S GUS Binary Vector',
        description: '12.76 kb plant binary plasmid with T-DNA borders, 35S-driven GUSPlus cassette, HygR, dual origins, and KanR.',
        totalLength: 12760,
        seed: 'pcambia-demo',
        features: [
            { name: 'LB border', type: 'misc_feature', start: 50, length: 30 },
            { name: '35S promoter', type: 'promoter', start: 320, length: 460 },
            { name: 'GUSPlus CDS', type: 'CDS', start: 900, length: 1812 },
            { name: 'NOS terminator', type: 'terminator', start: 2810, length: 250 },
            { name: 'HygR', type: 'CDS', start: 3620, length: 1026, strand: -1 },
            { name: 'NOS promoter', type: 'promoter', start: 4740, length: 320 },
            { name: 'RB border', type: 'misc_feature', start: 5400, length: 30 },
            { name: 'pVS1 ori', type: 'rep_origin', start: 6980, length: 1150 },
            { name: 'ColE1 ori', type: 'rep_origin', start: 8700, length: 650 },
            { name: 'KanR', type: 'CDS', start: 9640, length: 816, strand: -1 },
        ],
        primers: [
            { name: '35S F', feature: '35S promoter', side: 'start', length: 24, strand: 1 },
            { name: 'GUS R', feature: 'GUSPlus CDS', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'BacMam CMV Spike Delivery',
        description: '8.92 kb BacMam-style delivery plasmid with CMV promoter, viral spike payload, polyA, bacmid homology arms, AmpR, and pUC origin.',
        totalLength: 8920,
        seed: 'bacmam-demo',
        features: [
            { name: 'CMV promoter', type: 'promoter', start: 260, length: 620 },
            { name: 'Signal peptide', type: 'CDS', start: 950, length: 90 },
            { name: 'Spike payload', type: 'CDS', start: 1050, length: 3819 },
            { name: 'SV40 polyA', type: 'terminator', start: 4960, length: 150 },
            { name: 'Bacmid arm L', type: 'misc_feature', start: 5840, length: 350 },
            { name: 'Bacmid arm R', type: 'misc_feature', start: 6360, length: 350 },
            { name: 'AmpR', type: 'CDS', start: 7250, length: 858, strand: -1 },
            { name: 'pUC ori', type: 'rep_origin', start: 8100, length: 520 },
        ],
        primers: [
            { name: 'CMV F', feature: 'CMV promoter', side: 'start', length: 24, strand: 1 },
            { name: 'Spike R', feature: 'Spike payload', side: 'end', length: 24, strand: -1 },
        ],
    },
    {
        name: 'Minicircle IL15 CAR Payload',
        description: '5.12 kb parental minicircle donor with MSCV-driven IL15-CAR cassette, recombination sites, KanR, and compact bacterial origin.',
        totalLength: 5120,
        seed: 'minicircle-demo',
        features: [
            { name: 'attB recomb site', type: 'misc_feature', start: 60, length: 44 },
            { name: 'MSCV promoter', type: 'promoter', start: 220, length: 420 },
            { name: 'IL15 payload', type: 'CDS', start: 710, length: 486 },
            { name: 'P2A linker', type: 'misc_feature', start: 1225, sequence: P2A_SEQUENCE },
            { name: 'CAR sensor', type: 'CDS', start: 1315, length: 1140 },
            { name: 'SV40 polyA', type: 'terminator', start: 2600, length: 130 },
            { name: 'attP recomb site', type: 'misc_feature', start: 2790, length: 50 },
            { name: 'KanR', type: 'CDS', start: 3200, length: 816, strand: -1 },
            { name: 'Compact ori', type: 'rep_origin', start: 4150, length: 420 },
        ],
        primers: [
            { name: 'MSCV F', feature: 'MSCV promoter', side: 'start', length: 24, strand: 1 },
            { name: 'CAR R', feature: 'CAR sensor', side: 'end', length: 24, strand: -1 },
        ],
    },
];

export const DEMO_PLASMIDS: SequenceData[] = DEMO_SPECS.map(buildConstruct);
