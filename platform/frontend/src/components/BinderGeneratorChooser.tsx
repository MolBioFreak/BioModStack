import { useState } from 'react';
import type { ExistingDeNovoGenerator } from './deNovoGeneratorSelection';

export type BinderNativeRoute = { modelId: string; mode: string } | { templateId: string };
type Modality = 'antibody' | 'protein' | 'ligand' | 'seeded';
const formats: Array<[Modality, string]> = [
    ['antibody', 'Antibody / VHH'], ['protein', 'General protein binder'],
    ['ligand', 'Ligand / nucleotide binding'], ['seeded', 'Seeded antibody-target complex'],
];

/** Select intent first; hand each engine to its own existing authoring contract. */
export function BinderGeneratorChooser({ generator, onSelect, onOpenNativeRoute }: {
    generator: ExistingDeNovoGenerator | null;
    onSelect: (generator: ExistingDeNovoGenerator) => void;
    onOpenNativeRoute?: (route: BinderNativeRoute) => void;
}) {
    const [format, setFormat] = useState<Modality>(generator === 'bindcraft2' ? 'protein' : generator === 'ppiflow' ? 'seeded' : 'antibody');
    const button = (label: string, engine: ExistingDeNovoGenerator) => <button type="button" aria-pressed={generator === engine}
        className="rounded-lg border p-3 text-left" onClick={() => onSelect(engine)}>{label}</button>;
    return <section aria-label="Binder modality and generation engine" className="space-y-3">
        <label className="block">Binder format / objective
            <select aria-label="Binder format / objective" className="block w-full rounded border p-2" value={format} onChange={event => setFormat(event.target.value as Modality)}>
                {formats.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
            {format === 'antibody' && <>{button('RFantibody Stack', 'rfantibody')}{button('BoltzGen VHH', 'boltzgen')}</>}
            {format === 'seeded' && button('PPIFlow Seeded', 'ppiflow')}
            {(format === 'protein' || format === 'antibody') && button('BindCraft2 campaign', 'bindcraft2')}
            {format === 'protein' && onOpenNativeRoute && <button type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ templateId: 'protein_modification_experimental' })}>RFD3 · native De Novo Design</button>}
            {format === 'ligand' && onOpenNativeRoute && [
                ['ligand_binder', 'BoltzGen · ligand binder'], ['ntp_binder', 'BoltzGen · nucleotide binder'],
                ['scaffold_around_ligand', 'BoltzGen · scaffold around ligand'], ['backbone_docking', 'BoltzGen · nucleotide docking'],
            ].map(([mode, label]) => <button key={mode} type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'boltzgen', mode })}>{label}</button>)}
        </div>
        <p className="text-sm text-[var(--text-secondary)]">Choose an engine explicitly. Changing this filter does not rewrite the current request. Native model settings and route limits remain in effect.</p>
    </section>;
}
