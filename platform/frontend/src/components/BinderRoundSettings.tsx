import { useEffect, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { fetchModelById } from '../lib/api';
import { NativeSetting } from './NativeBinderGeneration';
import { binderRoundDesigners, binderRoundPredictors, blindFixedParameters, changeRoundStage, hydrateBinderRound,
    roundParameterIsBound, roundStages, toggleRoundStage, withRoundCatalogs, type BinderRoundCatalog, type BinderRoundDraft } from '../lib/binderRound';

import { roundCatalogFields, roundControlGroups, roundControlLabel } from '../lib/binderRoundControls';

const names: Record<string, string> = { proteinmpnn: 'ProteinMPNN', fampnn: 'FA-MPNN', caliby_binder: 'Caliby', protenix: 'Protenix V2 (recommended)', boltz2: 'Boltz-2', esmfold2: 'ESMFold2' };
const models = [...binderRoundDesigners, ...binderRoundPredictors];
const input = 'rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 text-sm';

const parseRoles = (text: string) => text.split(',').map(value => value.trim()).filter(Boolean);
function ChainRoles({ role, value, onChange }: { role: string; value: string[]; onChange: (chains: string[]) => void }) {
    // Preserve an in-progress delimiter while the wire remains a typed array.
    const [text, setText] = useState(value.join(','));
    const identity = JSON.stringify(value);
    useEffect(() => { setText(previous => JSON.stringify(parseRoles(previous)) === identity ? previous : value.join(',')); }, [identity]);
    return <input className={input} aria-label={`Round ${role}`} value={text} onChange={event => { setText(event.target.value); onChange(parseRoles(event.target.value)); }} placeholder="Producer-declared roles" />;
}

/** Shared round composition, not a second model settings contract or result gate. */
export function BinderRoundSettings({ values, onChange }: {
    values: Record<string, UntypedApiValue>;
    onChange: (draft: BinderRoundDraft) => void;
}) {
    const queries = useQueries({ queries: models.map(id => ({ queryKey: ['binder-round-catalog', id],
        queryFn: async (): Promise<BinderRoundCatalog> => {
            const { data } = await fetchModelById(id);
            if (!Array.isArray(data.params)) throw new Error(`No typed settings returned for ${names[id]}.`);
            return { ...data, id };
        }, staleTime: 60_000, retry: false })) });
    const catalogs = queries.flatMap(query => query.data ? [query.data] : []);
    const original = hydrateBinderRound(values);
    const draft = withRoundCatalogs(original, catalogs);
    const serialized = JSON.stringify(draft);
    const originalSerialized = JSON.stringify({ binder_round: values.binder_round, binder_round_drafts: values.binder_round_drafts });
    useEffect(() => { if (serialized !== originalSerialized) onChange(JSON.parse(serialized)); }, [serialized, originalSerialized, onChange]);
    const request = draft.binder_round;
    const [editing, setEditing] = useState<Record<string, string>>({});
    const edit = (patch: Partial<Pick<typeof request, 'enabled' | 'binder_chains' | 'target_chains'>>) => onChange({ ...draft, binder_round: { ...request, ...patch } });
    return <section aria-label="Initial candidate round" className="space-y-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 text-[var(--text-primary)]">
        <h3 className="text-lg font-semibold">Initial candidate round</h3>
        <label className="flex items-center gap-2"><input aria-label="Automatic blind complex prediction" type="checkbox" checked={request.enabled} onChange={event => edit({ enabled: event.target.checked })} />Automatic blind complex prediction</label>
        <p className="text-sm text-[var(--text-secondary)]">{request.enabled
            ? 'Predict every emitted candidate and every designed sequence against the independently declared target states. No hidden top-N selection.'
            : 'Generation only: no round sequence design or prediction. Your follow-on settings remain saved.'}</p>
        <details open={request.enabled || undefined} data-round-settings><summary className="cursor-pointer">{request.enabled ? 'Round sampling and settings' : 'Retained round settings'}</summary>
        <div className="mt-4 space-y-4">
        <p className="text-sm text-[var(--text-secondary)]">Sequence design runs only for producer-declared backbone-only candidates. Sequence-bearing candidates go directly to prediction without redesign. Generated poses, structural templates and interface restraints are not prediction inputs; MSA is configured independently below.</p>
        {(['sequence_design', 'prediction'] as const).map(role => {
            const stages = roundStages(request[role]);
            const ids = role === 'sequence_design' ? binderRoundDesigners : binderRoundPredictors;
            const stage = stages.find(value => value.model_id === editing[role]) ?? stages[0];
            const catalog = catalogs.find(item => item.id === stage.model_id);
            const fields = catalog ? roundCatalogFields(catalog).filter(parameter => !roundParameterIsBound(parameter.name)) : [];
            const grouping = roundControlGroups(stage.model_id, fields, stage.params);
            const patch = (values: Record<string, UntypedApiValue>) => onChange(changeRoundStage(draft, role, stage.model_id, values));
            return <fieldset key={role} aria-label={role === 'sequence_design' ? 'Round sequence design' : 'Round complex prediction'} className="min-w-0 space-y-3 rounded-lg border border-[var(--border-primary)] p-4">
                <legend>{role === 'sequence_design' ? 'Backbone sequence designer' : 'Blind complex validator'}</legend>
                <p className="text-sm text-[var(--text-secondary)]">{stages.length} selected · Same launched round; the scheduler owns execution concurrency.</p>
                <select className={input} aria-label={role === 'sequence_design' ? 'Round sequence designer' : 'Round complex validator'} value={stage.model_id} onChange={event => {
                    const model = event.target.value;
                    setEditing(previous => ({ ...previous, [role]: model }));
                    if (stages.length === 1) onChange(changeRoundStage(draft, role, model));
                }}>
                    {!ids.some(id => id === stage.model_id) && <option value={stage.model_id} disabled>{stage.model_id} (saved; choose a supported model)</option>}
                    {(stages.length === 1 ? ids : stages.map(value => value.model_id)).map(id => <option key={id} value={id}>{names[id] ?? id}</option>)}
                </select>
                <div className="flex flex-wrap gap-3" aria-label="Selected round engines">
                    {ids.map(id => { const selected = stages.some(stage => stage.model_id === id); return <label key={id} className="flex items-center gap-2 text-sm">
                        <input type="checkbox" aria-label={`Include ${id} in round`} checked={selected} disabled={selected && stages.length === 1} onChange={() => onChange(toggleRoundStage(draft, role, id))} />
                        {names[id]}{selected ? ' · selected' : ''}
                    </label>; })}
                </div>
                <div data-round-engine={stage.model_id} className="space-y-3">
                {role === 'prediction' && <p className="text-sm text-[var(--text-secondary)]" data-round-msa-summary>{grouping.msa.summary}</p>}
                <div className="grid gap-5 md:grid-cols-2" data-round-primary>{grouping.primary.map(parameter => <NativeSetting key={parameter.name} parameter={roundControlLabel(parameter)} values={stage.params} onPatch={patch} chains={[...request.binder_chains, ...request.target_chains]} />)}</div>
                {queries[models.indexOf(stage.model_id as typeof models[number])]?.error && <p role="status">{queries[models.indexOf(stage.model_id as typeof models[number])].error?.message} Saved settings remain intact.</p>}
                {!catalog && <p role="status">Loading model-owned settings…</p>}
                {[...grouping.groups].map(([group, parameters]) => <details key={group} data-round-group={group}><summary className="cursor-pointer">{group.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase())}</summary>
                    <div className="mt-4 grid gap-5 md:grid-cols-2">{parameters.map(parameter =>
                        role === 'prediction' && blindFixedParameters.has(parameter.name)
                            ? <div key={parameter.name} data-round-fixed={parameter.name}><label className="flex gap-2"><input type="checkbox" aria-label={parameter.name} checked={false} disabled />{roundControlLabel(parameter).label}</label><p className="text-xs">Fixed off for blind prediction; no pose/template conditioning.</p></div>
                            : <NativeSetting key={parameter.name} parameter={{ ...roundControlLabel(parameter), ...(group.startsWith('Inactive ') ? { display_applicable: false } : {}) }} values={stage.params} onPatch={patch} chains={[...request.binder_chains, ...request.target_chains]} />)}</div>
                </details>)}
                <p className="text-xs text-[var(--text-secondary)]">Source structures, sequences and target identities come from the selected producer, not these sampling settings.</p>
                </div>
            </fieldset>;
        })}
        <details><summary className="cursor-pointer">Chain roles (optional explicit overrides)</summary>
            <p className="my-2 text-sm">Leave empty to use producer-declared roles. No roles are inferred from filenames or sequence similarity. Overrides do not replace the native target source or template.</p>
            {(['binder_chains', 'target_chains'] as const).map(role => <label className="mr-4 inline-flex flex-col gap-1" key={role}>{role === 'binder_chains' ? 'Binder chains' : 'Target chains'}<ChainRoles role={role} value={request[role]} onChange={chains => edit({ [role]: chains })} /></label>)}
        </details>
        </div></details>
    </section>;
}
