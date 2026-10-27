import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { Rfd3AdditionalConditions } from './Rfd3AdditionalConditions';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';

export const rfd3ExecutionKeys = ['diffusion_batch_size', 'inference_sampler', 'skip_existing', 'low_memory_mode', 'cleanup_guideposts', 'cleanup_virtual_atoms', 'read_sequence_from_sequence_head', 'align_trajectory_structures', 'dump_prediction_metadata_json', 'write_full_json'] as const;
export function retainedRfd3Settings(value: Record<string, unknown> = {}) {
    return Object.fromEntries(rfd3ExecutionKeys.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
}

// General generation owns nested conditioning and exact source selectors, not
// flat redesign conditions. Keep this finite projection separate from execution.
export function retainedRfd3Draft(value: Record<string, unknown> = {}) {
    const keys = ['conditioning', 'input_structure', 'source_mapping', 'source_model', 'generation_mode'];
    return { ...retainedRfd3Settings(value), ...Object.fromEntries(keys.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]])) };
}
export function useRfd3GenerationBounds(enabled: boolean) {
    return useQuery({ queryKey: ['other-denovo-settings', 'rfd3', 'unconditional_monomer'], enabled,
        queryFn: async () => (await api.get<{ generation: Record<string, Schema> }>('/api/models/protein_modification_experimental/generation-settings', { params: { mode: 'de_novo_design', generator: 'rfd3', task: 'unconditional_monomer' } })).data,
        staleTime: 60_000, retry: false }).data?.generation;
}

/** Presentation groups only. Types and scientific defaults come from the current model owner. */
export function OtherDeNovoSettings({ model, task, value, onChange, sourceConditions = false, generalConditions = false, primaryOverrideFields = [] }: {
    sourceConditions?: boolean; generalConditions?: boolean;
    primaryOverrideFields?: string[];
    model: 'rfd3' | 'laproteina' | 'disco'; task: string; value: Record<string, unknown>;
    onChange: (value: Record<string, unknown>) => void;
}) {
    const inventory = useQuery({ queryKey: ['other-denovo-settings', model, task], queryFn: async () => (await api.get<{
        conditioning?: Record<string, Schema>;
        parameter_schema?: Schema; execution?: Record<string, Schema & { authority?: string; default_source?: string }>;
        profiles?: Record<string, { ld: string; ae: string; task: string }>;
    }>('/api/models/protein_modification_experimental/generation-settings', { params: { mode: 'de_novo_design', generator: model, task } })).data, staleTime: 60_000, retry: false });
    const key = `pcad_${model}_settings_json`;
    const settings = model === 'rfd3' ? value : value[key];
    const object = settings && typeof settings === 'object' && !Array.isArray(settings) ? settings as Record<string, unknown> : {};
    const root: Schema = inventory.data?.parameter_schema ?? { type: 'object', properties: inventory.data?.execution ?? {} };
    const patch = (field: string, next: unknown) => {
        const updated = { ...object }; if (next === undefined) delete updated[field]; else updated[field] = next;
        onChange(model === 'rfd3' ? updated : { ...value, [key]: updated });
    };
    const group = (field: string) => model === 'rfd3'
        ? field === 'inference_sampler' ? 'Sampler and classifier-free guidance' : 'Execution and native outputs'
        : model === 'laproteina'
            ? field.startsWith('bb_ca_') ? 'Backbone sampler' : field.startsWith('local_latents_') ? 'Local latent sampler' : /motif|contig|segment|atom_selection/.test(field) ? 'Motif overrides' : 'Sampling and outputs'
            : field === 'noisy_guidance' ? 'Cross-modal guidance' : field === 'sequence_sampling' ? 'Sequence sampling' : /confidence|output|duplicates/.test(field) ? 'Confidence and outputs' : 'Diffusion and seeds';
    const groups = new Map<string, [string, Schema][]>();
    for (const [field, schema] of Object.entries(root.properties ?? {})) {
        if (model === 'rfd3' && field === 'dump_trajectories') continue; // Existing primary control.
        if (primaryOverrideFields.includes(field)) continue; // The active-value control owns the same override.
        const typedSchema = model === 'rfd3' && field === 'inference_sampler' ? { ...schema, properties: Object.fromEntries(Object.entries(schema.properties ?? {}).map(([name, leaf]) => [name, { anyOf: [leaf.type === 'string_array' ? { ...leaf, type: 'array', items: { type: 'string' } } : leaf, { type: 'null' }] }])) } : schema;
        const label = group(field); groups.set(label, [...(groups.get(label) ?? []), [field, typedSchema]]);
    }
    return <section aria-label={`${model} native settings`} className="space-y-3">
        <p className="text-sm">Unchanged settings use the selected profile.</p>
        {model !== 'rfd3' && <p className="text-xs">Effective defaults are not reported by this profile.</p>}
        {model !== 'rfd3' && Object.keys(object).length > 0 && <p className="text-sm">Explicit overrides take precedence over primary controls where applicable.</p>}
        {inventory.error && <p role="status">Settings discovery failed: {inventory.error.message}. Retained values are unchanged.</p>}
        {settings != null && (typeof settings !== 'object' || Array.isArray(settings)) && <p role="status">Retained non-object settings: {JSON.stringify(settings)}. Edit a setting only to deliberately replace this value.</p>}
        {[...groups].map(([title, fields]) => <NativeSettingsDisclosure key={title} summary={title}>{() => <div className="space-y-3">{fields.map(([field, schema]) => {
            const definition = inventory.data?.execution?.[field];
            return <div key={field} data-native-setting={field} title={`Parameter key: ${field}`}>
                {definition?.authority === 'profile' ? <p>{field}: {JSON.stringify(object[field] ?? definition.default)} · profile-owned; retained explicit value is unchanged.</p> : <BioXpSchemaInput label={model === 'rfd3' ? field : schema.title ?? field} nativeLabels schema={schema} rootSchema={root} value={object[field]} onChange={next => patch(field, next)} fallback={() => <p>Retained {field}: {JSON.stringify(object[field])}</p>} />}
                {definition?.default_source && <p className="text-xs">Inherited reference: {JSON.stringify(definition.default)} · {definition.default_source}</p>}
                {Object.hasOwn(object, field) && definition?.authority !== 'profile' && <button type="button" onClick={() => patch(field, undefined)}>Use profile value</button>}
            </div>;
        })}</div>}</NativeSettingsDisclosure>)}
        {sourceConditions && !generalConditions && inventory.data?.conditioning && <Rfd3AdditionalConditions definitions={inventory.data.conditioning} value={value} onChange={onChange} />}
        {generalConditions && model === 'rfd3' && inventory.data?.conditioning && <>
            {Object.hasOwn(value, 'conditioning') && (value.conditioning === null || typeof value.conditioning !== 'object' || Array.isArray(value.conditioning)) && <p role="status">Retained conditioning: {JSON.stringify(value.conditioning)}. Editing a condition replaces this value.</p>}
            <Rfd3AdditionalConditions general definitions={inventory.data.conditioning} value={value.conditioning && typeof value.conditioning === 'object' && !Array.isArray(value.conditioning) ? value.conditioning as Record<string, unknown> : {}} onChange={conditioning => onChange({ ...value, conditioning })} />
            {Object.hasOwn(value, 'conditioning') && <button type="button" onClick={() => { const next = { ...value }; delete next.conditioning; onChange(next); }}>Omit conditioning</button>}
        </>}
        {model === 'laproteina' && inventory.data?.profiles && <details><summary>Native checkpoint pairs (generation, not evaluation)</summary>{Object.entries(inventory.data.profiles).map(([name, profile]) => <p key={name}>{name} · {profile.task} · {profile.ld} / {profile.ae}</p>)}</details>}
    </section>;
}
