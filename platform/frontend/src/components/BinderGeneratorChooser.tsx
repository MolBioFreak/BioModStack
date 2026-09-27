import { useState } from 'react';
import type { SelectedTarget } from './TargetAntigenSelector';
import type { ExistingDeNovoGenerator } from './deNovoGeneratorSelection';

export interface BinderSourceHandoff {
    target?: { reference?: Omit<SelectedTarget, 'file'>; path: string; name?: string; source?: string; modelNumber?: number | null; chain?: string | null; residues?: string[] };
    framework?: { path: string; name?: string };
}
export type BinderNativeRoute = ({ modelId: string; mode: string } | { templateId: string }) & {
    /** Destination-owned values take precedence over compatible inherited sources. */
    initialDraft?: Record<string, UntypedApiValue>;
    sources?: BinderSourceHandoff;
};
type Modality = 'antibody' | 'protein' | 'ligand' | 'seeded';
const formats: Array<[Modality, string]> = [
    ['antibody', 'Antibody / VHH'], ['protein', 'General protein binder'],
    ['ligand', 'Ligand / nucleotide binding'], ['seeded', 'Existing PPIFlow partial-flow request'],
];

/** Select intent first; hand each engine to its own existing authoring contract. */
export function BinderGeneratorChooser({ generator, onSelect, onOpenNativeRoute }: {
    generator: ExistingDeNovoGenerator | null;
    onSelect: (generator: ExistingDeNovoGenerator) => void;
    onOpenNativeRoute?: (route: BinderNativeRoute) => void;
}) {
    const [format, setFormat] = useState<Modality>(generator === 'bindcraft2' ? 'protein' : generator === 'ppiflow' ? 'seeded' : 'antibody');
    const button = (label: string, engine: ExistingDeNovoGenerator) => <button type="button" aria-pressed={generator === engine}
        className="rounded-lg border p-3 text-left" onClick={() => engine === 'boltzgen' && onOpenNativeRoute
            ? onOpenNativeRoute({ modelId: 'boltzgen', mode: 'nanobody_binder' }) : onSelect(engine)}>{label}</button>;
    return <section aria-label="Binder modality and generation engine" className="space-y-3">
        <label className="block">Binder format / objective
            <select aria-label="Binder format / objective" className="block w-full rounded border p-2" value={format} onChange={event => setFormat(event.target.value as Modality)}>
                {formats.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
            {format === 'antibody' && <>{button('RFantibody Stack', 'rfantibody')}{button('BoltzGen', 'boltzgen')}{onOpenNativeRoute && <><button type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'ppiflow', mode: 'antibody_binder' })}>PPIFlow · antibody generation</button><button type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'ppiflow', mode: 'nanobody_binder' })}>PPIFlow · nanobody generation</button></>}</>}
            {format === 'seeded' && button('PPIFlow · retained partial flow', 'ppiflow')}
            {(format === 'protein' || format === 'antibody') && button('BindCraft2 campaign', 'bindcraft2')}
            {format === 'protein' && onOpenNativeRoute && <>
                <button type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'boltzgen', mode: 'protein_binder' })}>BoltzGen · protein binder</button>
                <button type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'boltzgen', mode: 'peptide_binder' })}>BoltzGen · peptide</button>
                <button type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'ppiflow', mode: 'protein_binder' })}>PPIFlow · protein binder generation</button>
            </>}
            {format === 'ligand' && onOpenNativeRoute && [
                ['ligand_binder', 'BoltzGen · ligand binder'], ['ntp_binder', 'BoltzGen · nucleotide binder'],
                ['scaffold_around_ligand', 'BoltzGen · scaffold around ligand'], ['backbone_docking', 'BoltzGen · nucleotide docking'],
            ].map(([mode, label]) => <button key={mode} type="button" className="rounded-lg border p-3 text-left" onClick={() => onOpenNativeRoute({ modelId: 'boltzgen', mode })}>{label}</button>)}
        </div>
        <p className="text-sm text-[var(--text-secondary)]">Choose an engine explicitly. Changing this filter does not rewrite the current request. Native model settings and route limits remain in effect.</p>
    </section>;
}
