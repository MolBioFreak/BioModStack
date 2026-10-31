import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api, type NativeGenerationSourceBinding, type NativeGenerationSourceBindings, type StructureSourceSelection } from '../lib/api';
import { NativeSourceInput } from './DeNovoNativeSources';
import { continuationHref } from '../lib/deNovoContinuation';

const ENGINES = [['mpnn', 'ProteinMPNN'], ['fampnn', 'FAMPNN']] as const;
// Designers with their own forms: the chosen structure is handed over with the model preselected.
const OTHER_DESIGNERS = [
    ['ligandmpnn:ligand_aware', 'LigandMPNN: ligand-aware'], ['ligandmpnn:ntp_aware', 'LigandMPNN: NTP-aware'],
    ['ligandmpnn:metal_aware', 'LigandMPNN: metal-aware'], ['ligandmpnn:dna_aware', 'LigandMPNN: DNA-aware'],
    ['protonpottsmpnn:redesign', 'ProtonPottsMPNN: pH-specific redesign'],
    ['caliby_experimental:ensemble_design', 'Caliby: ensemble design'], ['caliby_experimental:sidechain_pack', 'Caliby: side-chain packing'],
] as const;
const VALIDATORS = [['esmfold2', 'ESMFold2'], ['protenix_v2', 'Protenix'], ['boltz2', 'Boltz-2']] as const;

/** Resequence existing designs from any generator, then validate. Positions are caller-selected. */
export function ResequencePanel() {
    const navigate = useNavigate();
    const [bindings, setBindings] = useState<NativeGenerationSourceBindings | undefined>();
    const [engine, setEngine] = useState<string>('mpnn');
    const [chains, setChains] = useState('A');
    const [fixed, setFixed] = useState('');
    const [designable, setDesignable] = useState('');
    const [validators, setValidators] = useState<string[]>(['esmfold2']);
    const [name, setName] = useState('Resequence');
    const [error, setError] = useState<string | null>(null);
    const binding = bindings?.input;
    const source: StructureSourceSelection | null = binding && !('kind' in binding) ? binding : null;
    const submit = useMutation({
        mutationFn: async () => (await api.post<{ id: string }>('/api/jobs/resequence/from-structures', {
            sources: [source], seq_method: engine as 'mpnn' | 'fampnn', design_chains: chains.trim(), name: name.trim() || 'Resequence',
            structure_validators: validators,
            ...(fixed.trim() ? { fixed_positions: fixed.trim() } : {}),
            ...(designable.trim() ? { designable_positions: designable.trim() } : {}),
        })).data,
        onSuccess: () => navigate('/'),
        onError: (cause: Error) => setError(cause.message || 'Failed to start resequencing'),
    });
    const toggle = (id: string) => setValidators(previous => previous.includes(id) ? previous.filter(x => x !== id) : [...previous, id]);
    const handoff = OTHER_DESIGNERS.some(([value]) => value === engine);
    const ready = Boolean(source) && chains.trim() !== '' && validators.length > 0 && !(fixed.trim() && designable.trim());
    return <section className="space-y-3" aria-label="Resequence and validate" data-resequence-panel>
        <p className="text-xs text-[var(--text-secondary)]">Resequence a design from any generator, then validate it. The loop pauses for review when it finishes. LigandMPNN, ProtonPottsMPNN and Caliby open their own forms with this structure.</p>
        <NativeSourceInput integrated label="Structure to resequence" value={source ? 'input' : ''} bindings={bindings}
            onChange={slot => { if (slot === '') setBindings(undefined); }}
            onAcquire={(next: NativeGenerationSourceBinding) => { setBindings({ input: next }); return 'input'; }} />
        <div className="grid grid-cols-2 gap-3">
            <label>Sequence engine<select aria-label="Resequence engine" value={engine} onChange={event => setEngine(event.target.value)}>
                <optgroup label="Runs in this loop">{ENGINES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</optgroup>
                <optgroup label="Opens its own form">{OTHER_DESIGNERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</optgroup></select></label>
            <label>Design chain(s)<input aria-label="Resequence design chains" value={chains} onChange={event => setChains(event.target.value)} /></label>
            <label>Keep these residues<input aria-label="Resequence fixed positions" placeholder="A1-20,B5" value={fixed} onChange={event => setFixed(event.target.value)} /></label>
            <label>Or change only these<input aria-label="Resequence designable positions" placeholder="A21-40" value={designable} onChange={event => setDesignable(event.target.value)} /></label>
        </div>
        <p className="text-xs text-[var(--text-secondary)]">Leave both empty to redesign every residue of the design chains. Fill one, not both.</p>
        <fieldset><legend>Validate with</legend>
            {VALIDATORS.map(([id, label]) => <label key={id} className="mr-4"><input type="checkbox" aria-label={`Validate with ${label}`} checked={validators.includes(id)} onChange={() => toggle(id)} /> {label}</label>)}
        </fieldset>
        <label>Job name<input aria-label="Resequence job name" value={name} onChange={event => setName(event.target.value)} /></label>
        {error && <p role="alert">{error}</p>}
        {handoff
            ? (source ? <a className="underline" aria-label="Open the selected designer" href={continuationHref(source, 'sequence', engine.split(':') as [string, string])}>Open in {OTHER_DESIGNERS.find(([value]) => value === engine)![1]}</a>
                : <p className="text-xs">Choose a structure first.</p>)
            : <button type="button" disabled={!ready || submit.isPending} onClick={() => { setError(null); submit.mutate(); }}>Run resequence and validate</button>}
    </section>;
}
