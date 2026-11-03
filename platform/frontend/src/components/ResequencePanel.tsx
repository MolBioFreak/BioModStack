import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api, submitJob, type Job, type NativeGenerationSourceBinding, type NativeGenerationSourceBindings, type StructureSourceSelection } from '../lib/api';
import { NativeSourceInput } from './DeNovoNativeSources';
import { DesignerSettingsForm } from './DesignerSettingsForm';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';

const ENGINES = [['mpnn', 'ProteinMPNN'], ['fampnn', 'FAMPNN'], ['ligandmpnn', 'LigandMPNN'], ['caliby', 'Caliby'], ['protonpottsmpnn', 'ProtonPottsMPNN (pH-specific redesign)']] as const;
const LIGAND_MODES = [['ligand_aware', 'Ligand-aware'], ['ntp_aware', 'NTP-aware'], ['metal_aware', 'Metal-aware'], ['dna_aware', 'DNA-aware']] as const;
const VALIDATORS = [['protenix_v2', 'Protenix v2'], ['esmfold2', 'ESMFold2 (optional)'], ['boltz2', 'Boltz-2']] as const;

/** Resequence existing designs from any generator, then validate. Positions are caller-selected. */
export function ResequencePanel() {
    const navigate = useNavigate();
    const [bindings, setBindings] = useState<NativeGenerationSourceBindings | undefined>();
    const [engine, setEngine] = useState<string>('mpnn');
    const [ligandMode, setLigandMode] = useState('ligand_aware');
    const [designer, setDesigner] = useState<{ values: Record<string, unknown>; valid: boolean }>({ values: {}, valid: true });
    const [validatorSettings, setValidatorSettings] = useState<Record<string, { values: Record<string, unknown>; valid: boolean }>>({});
    const [chains, setChains] = useState('A');
    const [fixed, setFixed] = useState('');
    const [designable, setDesignable] = useState('');
    const [validators, setValidators] = useState<string[]>(['protenix_v2']);
    const [name, setName] = useState('Resequence');
    // Each launch picks its own placement: Local, or any ready worker. A source that finished on one worker does not tie the next round to it.
    const [targetId, setTargetId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const binding = bindings?.input;
    const source: StructureSourceSelection | null = binding && !('kind' in binding) ? binding : null;
    const submit = useMutation({
        mutationFn: async () => {
            const request = {
                sources: [source], seq_method: engine,
                ...(engine === 'ligandmpnn' ? { designer_mode: ligandMode } : {}),
                ...(ownSettings && Object.keys(designer.values).length ? { designer_settings: designer.values } : {}), design_chains: chains.trim(), name: name.trim() || 'Resequence',
                structure_validators: validators,
                ...(Object.keys(sentValidatorSettings).length ? { validator_settings: sentValidatorSettings } : {}),
                ...(fixed.trim() ? { fixed_positions: fixed.trim() } : {}),
                ...(designable.trim() ? { designable_positions: designable.trim() } : {}),
            };
            if (!targetId) return (await api.post<{ id: string }>('/api/jobs/resequence/from-structures', request)).data;
            // A worker launch stages the structures here first, then goes through the usual reviewed execution plan with the chosen worker.
            const staged = (await api.post<Partial<Job>>('/api/jobs/resequence/from-structures/prepare', request)).data;
            return (await submitJob({ ...staged, execution_target_id: targetId }, { launchContext: false })).data as { id: string };
        },
        onSuccess: () => navigate('/'),
        onError: (cause: Error) => setError(cause.message || 'Failed to start resequencing'),
    });
    const toggle = (id: string) => setValidators(previous => previous.includes(id) ? previous.filter(x => x !== id) : [...previous, id]);
    const ownSettings = ['mpnn', 'fampnn', 'ligandmpnn', 'caliby', 'protonpottsmpnn'].includes(engine);
    // Only selected validators' non-empty settings are sent; deselecting a validator drops its settings.
    const sentValidatorSettings = Object.fromEntries(validators
        .filter(id => Object.keys(validatorSettings[id]?.values ?? {}).length > 0)
        .map(id => [id, validatorSettings[id].values]));
    const validatorsValid = validators.every(id => validatorSettings[id]?.valid !== false);
    const ready = Boolean(source) && chains.trim() !== '' && validators.length > 0 && !(fixed.trim() && designable.trim())
        && (!ownSettings || designer.valid) && validatorsValid;
    return <section className="space-y-3" aria-label="Resequence and validate" data-resequence-panel>
        <p className="text-xs text-[var(--text-secondary)]">Resequence a design from any generator, then validate it. The loop pauses for review when it finishes. ProtonPottsMPNN picks its own sites, so keep/change positions are not applied to it.</p>
        <NativeSourceInput integrated label="Structure to resequence" value={source ? 'input' : ''} bindings={bindings}
            onChange={slot => { if (slot === '') setBindings(undefined); }}
            onAcquire={(next: NativeGenerationSourceBinding) => { setBindings({ input: next }); return 'input'; }} />
        <div className="grid grid-cols-2 gap-3">
            <label>Sequence engine<select aria-label="Resequence engine" value={engine} onChange={event => setEngine(event.target.value)}>
                {ENGINES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Design chain(s)<input aria-label="Resequence design chains" value={chains} onChange={event => setChains(event.target.value)} /></label>
            <label>Keep these residues<input aria-label="Resequence fixed positions" placeholder="A1-20,B5" value={fixed} onChange={event => setFixed(event.target.value)} /></label>
            <label>Or change only these<input aria-label="Resequence designable positions" placeholder="A21-40" value={designable} onChange={event => setDesignable(event.target.value)} /></label>
        </div>
        <p className="text-xs text-[var(--text-secondary)]">Leave both empty to redesign every residue of the design chains. Fill one, not both.</p>
        <fieldset><legend>Validate with</legend>
            {VALIDATORS.map(([id, label]) => <label key={id} className="mr-4"><input type="checkbox" aria-label={`Validate with ${label}`} checked={validators.includes(id)} onChange={() => toggle(id)} /> {label}</label>)}
        </fieldset>
        {VALIDATORS.filter(([id]) => validators.includes(id)).map(([id, label]) =>
            <details key={id} className="rounded border border-[var(--border-primary)] p-3" data-validator-settings={id}>
                <summary className="cursor-pointer text-sm font-medium">{label} settings</summary>
                <DesignerSettingsForm engine={id} mode="" onChange={(values, valid) => setValidatorSettings(previous => ({ ...previous, [id]: { values, valid } }))} />
            </details>)}
        <label>Job name<input aria-label="Resequence job name" value={name} onChange={event => setName(event.target.value)} /></label>
        <p className="text-xs text-[var(--text-secondary)]">Run this round on this system or on any ready worker, whatever ran the earlier round.</p>
        <ExecutionTargetPicker value={targetId ?? ''} onChange={setTargetId} disabled={submit.isPending} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
        {error && <p role="alert">{error}</p>}
        {engine === 'ligandmpnn' && <label>LigandMPNN mode<select aria-label="LigandMPNN mode" value={ligandMode} onChange={event => setLigandMode(event.target.value)}>
            {LIGAND_MODES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
        {ownSettings && <DesignerSettingsForm key={`${engine}:${ligandMode}`} engine={engine} mode={ligandMode} onChange={(values, valid) => setDesigner({ values, valid })} />}
        <button type="button" disabled={!ready || submit.isPending} onClick={() => { setError(null); submit.mutate(); }}>Run resequence and validate</button>
    </section>;
}
