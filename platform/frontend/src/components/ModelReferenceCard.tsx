// Public maintainer references; display metadata only, never scientific input.
const references: Record<string, { name: string; description: string; github: string; paper: string; docs: string }> = {
    rfd4_proteina: { name: 'RFD4-Proteina', description: 'Protein sequence–structure co-design', github: 'https://github.com/RosettaCommons/RFD4-Proteina', paper: 'https://rfd4-proteina.org/assets/paper/RFD4-Proteina.pdf', docs: 'https://rfd4-proteina.org/latest/' },
    rfd3: { name: 'RFD3', description: 'All-atom conditioned protein backbone design', github: 'https://github.com/RosettaCommons/foundry', paper: 'https://www.biorxiv.org/content/early/2025/11/19/2025.09.18.676967', docs: 'https://rosettacommons.github.io/foundry/models/rfd3/input.html' },
    disco: { name: 'DISCO', description: 'Protein sequence–structure co-design with molecular context', github: 'https://github.com/DISCO-design/DISCO', paper: 'https://arxiv.org/abs/2604.05181', docs: 'https://github.com/DISCO-design/DISCO#readme' },
    laproteina: { name: 'La-Proteina', description: 'Atomistic protein generation and motif scaffolding', github: 'https://github.com/NVIDIA-BioNeMo/la-proteina', paper: 'https://arxiv.org/abs/2507.09466', docs: 'https://github.com/NVIDIA-BioNeMo/la-proteina#inference' },
    complexa: { name: 'Complexa', description: 'Atomistic protein design and native search', github: 'https://github.com/NVIDIA-BioNeMo/Proteina-Complexa', paper: 'https://arxiv.org/abs/2603.27950', docs: 'https://github.com/NVIDIA-BioNeMo/Proteina-Complexa#readme' },
};

export function ModelReferenceCard({ model }: { model: string }) {
    const reference = references[model];
    if (!reference) return null;
    return <aside aria-label={`${reference.name} references`} className="rounded-lg border border-[var(--border-primary)] px-3 py-2 text-xs">
        <strong>{reference.name}</strong><p className="text-[var(--text-secondary)]">{reference.description}</p>
        <div className="mt-1 flex gap-3">{([['GitHub', reference.github], ['Preprint', reference.paper], ['Docs', reference.docs]] as const).map(([label, href]) =>
            <a key={label} href={href} target="_blank" rel="noopener noreferrer" className="underline">{label}</a>)}</div>
    </aside>;
}
