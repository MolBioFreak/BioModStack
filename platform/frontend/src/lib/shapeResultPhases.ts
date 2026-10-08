export interface StructureFile {
    name: string;
    filename: string;
    path: string;
    type: 'pdb' | 'cif';
    size_bytes: number;
}

export interface ShapePhaseArtifact extends StructureFile {
    engine: string;
    role: string;
}

export interface ShapeResultPhase {
    key: 'backbone_generation' | 'sequence_design' | 'structure_validation';
    label: string;
    description: string;
    artifacts: ShapePhaseArtifact[];
}

function engineFromCandidate(path: string): string {
    if (path.includes('__fampnn__')) return 'FAMPNN → ESMFold2';
    if (path.includes('__proteinmpnn__')) return 'ProteinMPNN → ESMFold2';
    return 'ESMFold2';
}

export function classifyShapeStructureFiles(files: StructureFile[]): ShapeResultPhase[] {
    const canonicalCandidates = files.filter((file) =>
        file.path.includes('/results/shape_candidates/'));
    const source = canonicalCandidates.find((file) => file.filename.endsWith('.source.pdb'));
    const fampnnSample = files.find((file) =>
        file.type === 'pdb'
        && file.path.includes('/run/shape_sequences/fampnn/')
        && file.path.includes('/runtime/samples/'));
    const validation = canonicalCandidates
        .filter((file) => file.type === 'cif')
        .map((file) => ({ ...file, engine: engineFromCandidate(file.path), role: 'ESMFold2 validation/refold' }));

    return [
        {
            key: 'backbone_generation',
            label: '1. Backbone generation',
            description: 'Shape-guided backbone generated from the reviewed CAD geometry.',
            artifacts: source ? [{ ...source, engine: 'RFD3', role: 'Generated backbone' }] : [],
        },
        {
            key: 'sequence_design',
            label: '2. Sequence design',
            description: 'Sequence-engine output before independent structure validation.',
            artifacts: fampnnSample ? [{ ...fampnnSample, engine: 'FAMPNN', role: 'Sequence-designed structure' }] : [],
        },
        {
            key: 'structure_validation',
            label: '3. Structure validation',
            description: 'ESMFold2 refolds grouped by the sequence engine that produced each sequence.',
            artifacts: validation,
        },
    ];
}
