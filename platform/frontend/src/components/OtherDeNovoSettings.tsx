import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { NativeSettingsDisclosure } from './NativeSettingsDisclosure';

/** Presentation groups only. Types and scientific defaults come from the current model owner. */
export function OtherDeNovoSettings({ model, task, value, onChange, primaryOverrideFields = [], selectedPreset, requestParams = {} }: {
    primaryOverrideFields?: string[]; selectedPreset?: string; requestParams?: Record<string, unknown>;
    model: 'laproteina' | 'disco'; task: string; value: Record<string, unknown>;
    onChange: (value: Record<string, unknown>) => void;
}) {
    const inventory = useQuery({ queryKey: ['other-denovo-settings', model, task], queryFn: async () => (await api.get<{
        parameter_schema?: Schema; execution?: Record<string, Schema & { authority?: string; default_source?: string }>;
        profiles?: Record<string, { ld: string; ae: string; task: string }>;
        native_profile_defaults?: { source_revision?: string; composition?: string; profiles?: Record<string, Record<string, unknown>> };
        native_profile_reference?: { source_revision: string; composition: string; variants: Record<string, Record<string, { path: string; value: unknown }>> };
    }>('/api/models/protein_modification_experimental/generation-settings', { params: { mode: 'de_novo_design', generator: model, task } })).data, staleTime: 60_000, retry: false });
    const key = `pcad_${model}_settings_json`;
    const settings = value[key];
    const object = settings && typeof settings === 'object' && !Array.isArray(settings) ? settings as Record<string, unknown> : {};
    // Display metadata is private to this editor; it is never a requested value.
    const root: Schema = structuredClone(inventory.data?.parameter_schema ?? { type: 'object', properties: inventory.data?.execution ?? {} });
    const profile = model === 'laproteina' && selectedPreset ? inventory.data?.native_profile_defaults?.profiles?.[selectedPreset] : undefined;
    let legacySettings = requestParams[`${model}_settings`];
    if (typeof legacySettings === 'string') { try { legacySettings = JSON.parse(legacySettings); } catch { legacySettings = undefined; } }
    const legacyObject = legacySettings && typeof legacySettings === 'object' && !Array.isArray(legacySettings) ? legacySettings as Record<string, unknown> : {};
    const selector = (name: string, fallback: string) => {
        for (const [source, key] of [[object, name], [legacyObject, name], [requestParams, `disco_${name}`], [requestParams, `pcad_disco_${name}`]] as const) {
            if (Object.hasOwn(source, key)) return source[key];
        }
        return fallback;
    };
    const discoSelection = `${selector('experiment', 'designable')}/${selector('effort', 'fast')}`;
    const discoReference = model === 'disco' ? inventory.data?.native_profile_reference?.variants[discoSelection] : undefined;
    // Project only source-qualified leaves into schema metadata, not a synthetic
    // effective object. The shared renderer edits the sparse object separately.
    if (discoReference) for (const [field, typeName] of [['noisy_guidance', 'NoisyGuidance'], ['sequence_sampling', 'SequenceSampling']]) {
        const definition = root.$defs?.[typeName];
        for (const [name, child] of Object.entries(definition?.properties ?? {})) {
            const reference = discoReference[`${field}.${name}`];
            if (reference) Object.assign(child, { default: reference.value, default_source: `DISCO ${inventory.data?.native_profile_reference?.source_revision} · ${reference.path} · upstream reference, not effective configuration` });
        }
    }
    const patch = (field: string, next: unknown) => {
        const updated = { ...object }; if (next === undefined) delete updated[field]; else updated[field] = next;
        onChange({ ...value, [key]: updated });
    };
    const group = (field: string) => model === 'laproteina'
        ? field.startsWith('bb_ca_') ? 'Backbone sampler' : field.startsWith('local_latents_') ? 'Local latent sampler' : /motif|contig|segment|atom_selection/.test(field) ? 'Motif overrides' : 'Sampling and outputs'
        : field === 'noisy_guidance' ? 'Cross-modal guidance' : field === 'sequence_sampling' ? 'Sequence sampling' : /confidence|output|duplicates/.test(field) ? 'Confidence and outputs' : 'Diffusion and seeds';
    const groups = new Map<string, [string, Schema][]>();
    for (const [field, schema] of Object.entries(root.properties ?? {})) {
        if (primaryOverrideFields.includes(field)) continue; // The active-value control owns the same override.
        const label = group(field); groups.set(label, [...(groups.get(label) ?? []), [field, schema]]);
    }
    return <section aria-label={`${model} native settings`} className="space-y-3">
        <p className="text-sm">Unchanged settings use the selected profile.</p>
        {model === 'disco' && discoReference ? <p className="text-xs">Upstream reference for {discoSelection} · {inventory.data?.native_profile_reference?.composition} · revision {inventory.data?.native_profile_reference?.source_revision}. This is not effective configuration; BMS and user overrides take precedence.</p> : model === 'laproteina' && profile ? <p className="text-xs">Upstream reference for {selectedPreset} · {inventory.data?.native_profile_defaults?.composition} · revision {inventory.data?.native_profile_defaults?.source_revision}. This is not effective configuration; BMS and user overrides take precedence.</p> : <p className="text-xs">Profile reference values are not reported for this selection.</p>}
        {Object.keys(object).length > 0 && <p className="text-sm">Explicit overrides take precedence over primary controls where applicable.</p>}
        {inventory.error && <p role="status">Settings discovery failed: {inventory.error.message}. Retained values are unchanged.</p>}
        {settings != null && (typeof settings !== 'object' || Array.isArray(settings)) && <p role="status">Retained non-object settings: {JSON.stringify(settings)}. Edit a setting only to deliberately replace this value.</p>}
        {[...groups].map(([title, fields]) => <NativeSettingsDisclosure key={title} summary={title}>{() => <div className="space-y-3">{fields.map(([field, schema]) => {
            const definition = inventory.data?.execution?.[field];
            const hasOverride = Object.hasOwn(object, field);
            const flatKey = `${model}_${field}`;
            const alias = Object.hasOwn(requestParams, flatKey) ? flatKey : `pcad_${flatKey}`;
            const legacyNative = Object.hasOwn(legacyObject, field);
            const hasFlat = Object.hasOwn(requestParams, alias);
            const hasLegacy = legacyNative || hasFlat;
            let legacyValue = legacyNative ? legacyObject[field] : requestParams[alias];
            // The compiler projects DISCO's flat comma-separated shorthand into an array.
            if (!legacyNative && model === 'disco' && field === 'seeds' && typeof legacyValue === 'string') {
                const tokens = legacyValue.split(',').map(token => token.trim()).filter(Boolean);
                if (tokens.every(token => /^[+-]?\d+$/.test(token))) legacyValue = tokens.map(Number);
            }
            const sourceDefault = definition?.default_source && Object.hasOwn(definition, 'default') && ['boolean', 'integer', 'number', 'string'].includes(String(definition.type));
            const hasProfileReference = !!profile && Object.hasOwn(profile, field);
            const discoLeaf = discoReference?.[field];
            const nestedReference = model === 'disco' && ['noisy_guidance', 'sequence_sampling'].includes(field) && !!discoReference;
            const shown = hasOverride ? object[field] : hasLegacy ? legacyValue : nestedReference ? {} : hasProfileReference ? profile![field] : discoLeaf ? discoLeaf.value : sourceDefault ? definition!.default : undefined;
            return <div key={field} data-native-setting={field} title={`Parameter key: ${field}`}>
                {definition?.authority === 'profile' ? <p>{definition.title ?? field}: {JSON.stringify(hasOverride ? object[field] : definition.default)} · profile-owned; retained explicit value is unchanged.</p> : <BioXpSchemaInput label={schema.title ?? field} nativeLabels useSchemaTitles schema={schema} rootSchema={root} showInherited={nestedReference} value={shown} onChange={next => {
                    patch(field, next);
                }} fallback={() => <p>Retained {field}: {JSON.stringify(object[field])}</p>} />}
                {definition?.default_source && <p className="text-xs">Inherited reference: {JSON.stringify(definition.default)} · {definition.default_source}</p>}
                {hasProfileReference && <p className="text-xs">Upstream profile reference: {JSON.stringify(profile![field])}</p>}
                {discoLeaf && <p className="text-xs">Upstream YAML reference: {discoLeaf.path} = {JSON.stringify(discoLeaf.value)}</p>}
                {discoReference && Object.entries(discoReference).filter(([name]) => name.startsWith(`${field}.`)).map(([name, leaf]) => <p className="text-xs" key={name}>Upstream YAML reference: {leaf.path} = {JSON.stringify(leaf.value)}</p>)}
                <p className="text-xs">{hasOverride ? 'Custom override' : hasLegacy ? `Retained override: ${legacyNative ? `${model}_settings` : alias} · ${JSON.stringify(legacyValue)}` : nestedReference ? 'Default · upstream leaf references; not effective configuration' : hasProfileReference || discoLeaf ? 'Default · Upstream reference; not an override' : sourceDefault ? 'Default · Inherited reference; not an override' : 'Inherited value not reported'}</p>
                {hasOverride && definition?.authority !== 'profile' && <button type="button" onClick={() => patch(field, undefined)}>{hasLegacy ? 'Use retained override' : 'Use default'}</button>}
            </div>;
        })}</div>}</NativeSettingsDisclosure>)}
        {model === 'laproteina' && inventory.data?.profiles && <details><summary>Native checkpoint pairs (generation, not evaluation)</summary>{Object.entries(inventory.data.profiles).map(([name, profile]) => <p key={name}>{name} · {profile.task} · {profile.ld} / {profile.ae}</p>)}</details>}
    </section>;
}
