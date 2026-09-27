import { useState } from 'react';
import { FileBrowser } from './FileBrowser';
import { BioXpSchemaInput, schemaInitialValue } from './BioXpSchemaInput';
import { BioXpNumericInput } from './BioXpNumericInput';
import { NativeSetting } from './NativeBinderGeneration';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';
import { themedInputStyle } from './ProteinDesignWorkflow';
import type { ShapeSequenceSettingsDefinition } from '../lib/api';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { NativeBinderParameter } from '../lib/nativeBinderAuthoring';

type Definition = Pick<ShapeSequenceSettingsDefinition, 'params' | 'json_schema'>;
const primary = (name: string) => /^(num_timesteps|.*temperature|.*diffusion_samples|.*num_samples|.*n_sample|.*use_msa)$/.test(name);
const group = ({ name }: { name: string }) => {
    if (/checkpoint|model_name|weights/.test(name)) return 'Model and checkpoint';
    if (/gaussian|conformer/.test(name)) return 'Conformer perturbations';
    if (/potts/.test(name)) return 'Potts sampling';
    if (/scn_|repack|psce|sidechain|sequence_only/.test(name)) return 'Side-chain sampling';
    if (/relax/.test(name)) return 'Relaxation';
    if (/position|pos_|symmetry|omit|bias|exclude|constraint/.test(name)) return 'Sequence constraints';
    if (/msa|template/.test(name)) return 'MSA and templates';
    if (/step|recycl|noise|schedule/.test(name)) return 'Sampling schedule';
    if (/worker|batch|verbose|seed/.test(name)) return 'Execution and reproducibility';
    return 'Additional native settings';
};

/** Composition only. Discovery owns applicability, schemas and all defaults. */
export function ShapeNativeSettings({ definition, values, onPatch, inputSchema }: {
    definition?: Definition; values: Record<string, unknown>;
    onPatch: (patch: Record<string, unknown>) => void; inputSchema?: Record<string, unknown>;
}) {
    const [browse, setBrowse] = useState<string | null>(null);
    const root = (inputSchema ?? definition?.json_schema ?? {}) as Schema;
    const fields = inputSchema
        ? Object.entries(root.properties ?? {}).map(([name, schema]) => ({ name, type: schema.type ?? 'object', ...schema }))
        : definition?.params ?? [];
    const render = (metadata: UntypedApiValue) => {
        const name = metadata.name as string;
        const schema = root.properties?.[name] ?? metadata.schema;
        const value = Object.hasOwn(values, name) ? values[name] : metadata.default;
        const label = metadata.label ?? name.replace(/^(fampnn|mpnn|esmfold2|boltz2|protenix)_/, '').replaceAll('_', ' ').replace(/^./, (s: string) => s.toUpperCase());
        if (metadata.type === 'file' || metadata.type === 'directory') return <NativeSetting parameter={{ ...metadata, type: 'file', label } as NativeBinderParameter} values={values} chains={[]} onPatch={onPatch} onBrowse={setBrowse} />;
        // Preserve nested refs and nullable unions with the shared native schema editor.
        if (schema && (schema.$ref || schema.anyOf || schema.oneOf || Array.isArray(schema.type) || ['object', 'array'].includes(schema.type))) {
            if (value === undefined) return <div className="space-y-2"><span>{label} · service default</span><button type="button" className="block rounded border p-2" onClick={() => onPatch({ [name]: schemaInitialValue(schema, root) })}>Set {name}</button></div>;
            return <BioXpSchemaInput label={name} schema={schema} rootSchema={root} value={value}
                onChange={next => onPatch({ [name]: next })}
                fallback={() => <p role="status">No typed schema was advertised for {name}. The saved value is retained.</p>} />;
        }
        if (['number', 'integer'].includes(metadata.type) && !metadata.enum) return <div className="min-w-0 space-y-2" data-native-setting={name}>
            <label className="block text-sm font-medium">{label}<BioXpNumericInput aria-label={name} value={value}
                min={metadata.minimum ?? undefined} max={metadata.maximum ?? undefined} step={metadata.step ?? (metadata.type === 'integer' ? 1 : 'any')}
                onValueChange={next => onPatch({ [name]: next })} className="mt-2 w-full rounded-lg border p-2" style={themedInputStyle} /></label>
            {metadata.description && <p className="text-xs text-[var(--text-secondary)]">{metadata.description}</p>}
            <p className="text-xs text-[var(--text-secondary)]"><code>{name}</code>{metadata.default != null && ` · Native default: ${metadata.default}`}{metadata.minimum != null && ` · Minimum: ${metadata.minimum}`}{metadata.maximum != null && ` · Maximum: ${metadata.maximum}`}</p>
            {metadata.ui_control === 'slider' && metadata.minimum != null && metadata.maximum != null && typeof value === 'number' && <input type="range" aria-label={`${name} slider`} min={metadata.minimum} max={metadata.maximum} step={metadata.step ?? 'any'} value={value} onChange={event => onPatch({ [name]: Number(event.target.value) })} />}
        </div>;
        return <NativeSetting parameter={{ ...metadata, label } as NativeBinderParameter} values={values} chains={[]} onPatch={onPatch} />;
    };
    return <div className="min-w-0 space-y-4 [&_fieldset]:min-w-0 [&_input]:max-w-full [&_select]:max-w-full [&_fieldset]:border-[var(--border-primary)] [&_fieldset_input]:bg-[var(--bg-tertiary)] [&_fieldset_select]:bg-[var(--bg-tertiary)] [&_fieldset_p]:text-[var(--text-secondary)]">
        {browse && <FileBrowser title={`Select ${browse}`} onCancel={() => setBrowse(null)} onSelect={path => { onPatch({ [browse]: path }); setBrowse(null); }} />}
        <div className="grid gap-4 sm:grid-cols-2">{fields.filter(p => primary(p.name)).map(p => <div key={p.name} data-shape-native-field={p.name}>{render(p)}</div>)}</div>
        <SequenceDesignerSettings fields={fields.filter(p => !primary(p.name))} groupForField={group} initiallyOpenGroups={[]}
            renderField={p => <div data-shape-native-field={p.name}>{render(p)}</div>} />
    </div>;
}
