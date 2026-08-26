export interface DocumentationLink {
    label: string;
    href: string;
}

export const MODEL_DOCUMENTATION_LINKS = {
    alphafold2: [
        { label: 'AlphaFold GitHub', href: 'https://github.com/google-deepmind/alphafold' },
        { label: 'AlphaFold2 Nature DOI', href: 'https://doi.org/10.1038/s41586-021-03819-2' },
    ],

    boltz2: [
        { label: 'Boltz docs', href: 'https://docs.boltz.bio/' },
        { label: 'Boltz GitHub', href: 'https://github.com/jwohlwend/boltz' },
        { label: 'Boltz-2 preprint DOI', href: 'https://doi.org/10.1101/2025.06.14.659707' },
    ],
    boltzgen: [
        { label: 'BoltzGen GitHub', href: 'https://github.com/HannesStark/boltzgen' },
        { label: 'BoltzGen preprint DOI', href: 'https://doi.org/10.1101/2025.11.20.689494' },
    ],
    caliby: [
        { label: 'Caliby preprint DOI', href: 'https://doi.org/10.1101/2025.09.30.679633' },
    ],
    chai1: [
        { label: 'Chai-1 GitHub', href: 'https://github.com/chaidiscovery/chai-lab' },
        { label: 'Chai-1 preprint DOI', href: 'https://doi.org/10.1101/2024.10.10.615955' },
    ],
    confornets: [
        { label: 'ConforNets GitHub', href: 'https://github.com/aqlaboratory/confornets' },
        { label: 'ConforNets arXiv', href: 'https://arxiv.org/abs/2604.18559' },
    ],
    diffdock: [
        { label: 'DiffDock GitHub', href: 'https://github.com/gcorso/DiffDock' },
        { label: 'DiffDock arXiv', href: 'https://arxiv.org/abs/2210.01776' },
    ],
    disco: [
        { label: 'DISCO GitHub', href: 'https://github.com/DISCO-design/DISCO' },
        { label: 'DISCO arXiv', href: 'https://arxiv.org/abs/2604.05181' },
    ],
    esmfold2: [
        { label: 'Biohub ESM GitHub', href: 'https://github.com/Biohub/esm' },
        { label: 'ESMFold2-Fast HF', href: 'https://huggingface.co/biohub/ESMFold2-Fast' },
        { label: 'ESM protein paper', href: 'https://biohub.ai/papers/esm_protein.pdf' },
    ],
    fampnn: [
        { label: 'FAMPNN GitHub', href: 'https://github.com/richardshuai/fampnn' },
        { label: 'FAMPNN preprint DOI', href: 'https://doi.org/10.1101/2025.02.13.637498' },
    ],
    frustrampnn: [
        { label: 'FrustraMPNN GitHub', href: 'https://github.com/schoederlab/frustraMPNN' },
    ],
    fold_cp: [{ label: 'PyTorch DTensor', href: 'https://docs.pytorch.org/docs/stable/distributed.tensor.html' }],
    gromacs: [
        { label: 'GROMACS 2025.3 manual', href: 'https://manual.gromacs.org/2025.3/' },
    ],
    openmm: [
        { label: 'OpenMM user guide', href: 'https://docs.openmm.org/latest/userguide/' },
    ],
    laproteina: [
        { label: 'La-Proteina GitHub', href: 'https://github.com/NVIDIA-Digital-Bio/la-proteina' },
        { label: 'La-Proteina arXiv', href: 'https://arxiv.org/abs/2507.09466' },
    ],
    ligandmpnn: [
        { label: 'LigandMPNN GitHub', href: 'https://github.com/dauparas/LigandMPNN' },
        { label: 'LigandMPNN DOI', href: 'https://doi.org/10.1038/s41592-025-02626-1' },
    ],
    ppiflow: [
        { label: 'PPIFlow preprint DOI', href: 'https://doi.org/10.1101/2025.11.28.691195' },
    ],
    protein_hunter: [
        { label: 'Protein Hunter preprint DOI', href: 'https://doi.org/10.1101/2025.10.10.681530' },
    ],
    proteinmpnn: [
        { label: 'ProteinMPNN GitHub', href: 'https://github.com/dauparas/ProteinMPNN' },
        { label: 'ProteinMPNN Science DOI', href: 'https://doi.org/10.1126/science.add2187' },
    ],
    protenix: [
        { label: 'Protenix GitHub', href: 'https://github.com/bytedance/Protenix' },
        { label: 'Protenix preprint DOI', href: 'https://doi.org/10.1101/2025.01.08.631967' },
    ],
    rf3: [
        { label: 'RoseTTAFold All-Atom DOI', href: 'https://doi.org/10.1126/science.adl2528' },
        { label: 'RosettaCommons', href: 'https://github.com/RosettaCommons' },
    ],
    rfantibody: [
        { label: 'RFantibody GitHub', href: 'https://github.com/RosettaCommons/RFantibody' },
        { label: 'RFantibody preprint DOI', href: 'https://doi.org/10.1101/2024.03.14.585103' },
    ],
    rfdiffusion: [
        { label: 'RFdiffusion GitHub', href: 'https://github.com/RosettaCommons/RFdiffusion' },
        { label: 'RFdiffusion Science DOI', href: 'https://doi.org/10.1126/science.add2187' },
    ],
    rfdpoly: [
        { label: 'RFDpoly GitHub', href: 'https://github.com/RosettaCommons/RFDpoly' },
        { label: 'RFDpoly preprint DOI', href: 'https://doi.org/10.1101/2025.10.01.679929' },
    ],
    unidock: [
        { label: 'Uni-Dock GitHub', href: 'https://github.com/dptech-corp/Uni-Dock' },
        { label: 'Uni-Dock DOI', href: 'https://doi.org/10.1021/acs.jctc.2c01145' },
    ],
} as const;

export type ModelDocumentationTopic = keyof typeof MODEL_DOCUMENTATION_LINKS;

export const getModelDocumentationLinks = (topics: readonly ModelDocumentationTopic[]): DocumentationLink[] => {
    const seen = new Set<string>();
    const links: DocumentationLink[] = [];
    topics.forEach((topic) => {
        MODEL_DOCUMENTATION_LINKS[topic].forEach((link) => {
            if (seen.has(link.href)) return;
            seen.add(link.href);
            links.push(link);
        });
    });
    return links;
};
