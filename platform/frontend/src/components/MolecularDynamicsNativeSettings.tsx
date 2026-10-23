import { useState } from 'react';
import { FileBrowser } from './FileBrowser';
import { uploadFile } from '../lib/api';
import { parseNativeMdp, renderNativeMdp, type NativeMdp, type MolecularDynamicsNativeDraft, type MolecularDynamicsAnalysisDraft } from './molecularDynamicsUiState';

const inputClass = 'mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';
const buttonClass = 'rounded border border-slate-600 px-3 py-2 text-xs text-cyan-200';
import nativeParameterCatalog from '../../../../scripts/bms_md/native_parameters.json';

const sections: Record<string, string[]> = {};
for (const parameter of nativeParameterCatalog.parameters) {
    // Pull rows retain their existing count-aware editor below.
    if (parameter.section === 'COM pulling' || parameter.historical_only) continue;
    (sections[parameter.section] ??= []).push(parameter.key);
}
const nativeAliases: Record<string, string> = nativeParameterCatalog.runtime_reconciliation.native_aliases;
const canonicalKey = (key: string) => (nativeAliases[key] ?? key).toLowerCase().replaceAll('_', '-');
const parameterFor = (key: string) => nativeParameterCatalog.parameters.find(row => canonicalKey(row.key) === canonicalKey(key))
    ?? nativeParameterCatalog.parameters.find(row => row.index_family && canonicalKey(row.key).replace(/\d+/g, '#') === canonicalKey(key).replace(/\d+/g, '#'));
export function nativeSectionKeys(keys: string[], mdp: NativeMdp): string[] {
    const count = (key: string) => Math.max(1, Math.trunc(Number(mdp[key]) || 0));
    const expanded = keys.flatMap(key => {
        const family = parameterFor(key)?.index_family;
        if (family === 'awh') return Array.from({ length: count('awh-nbias') }, (_, n) => {
            const bias = key.replace(/^awh1-/, `awh${n + 1}-`);
            return bias.includes('-dim1-') ? Array.from({ length: count(`awh${n + 1}-ndim`) }, (_, d) => bias.replace('-dim1-', `-dim${d + 1}-`)) : [bias];
        }).flat();
        if (family === 'rotation') return Array.from({ length: count('rot-ngroups') }, (_, n) => key.replace(/0$/, String(n)));
        if (family === 'iontype') return Array.from({ length: count('iontypes') }, (_, n) => key.replace('iontype0-', `iontype${n}-`));
        return [key];
    });
    // Imported indexed rows remain typed even if a count was later reduced.
    return Array.from(new Set([...expanded, ...Object.keys(mdp).filter(key => keys.some(template => parameterFor(key)?.key === template))]));
}
function Text({ label, value, onChange, numeric = false }: { label: string; value: string | number | undefined; onChange: (s: string) => void; numeric?: boolean }) {
    return <label className="block text-xs text-slate-300">{label}<input aria-label={label} className={inputClass} type={numeric ? 'number' : 'text'} step={numeric ? 'any' : undefined} inputMode={numeric ? 'decimal' : undefined} value={value ?? ''} onChange={e => onChange(e.target.value)} /></label>;
}
export function MdManagedFile({ label, value, onChange }: { label: string; value?: string | null; onChange: (s: string) => void }) {
    const [browse, setBrowse] = useState(false);
    return <div><Text label={label} value={value ?? undefined} onChange={onChange} /><button type="button" className={buttonClass} onClick={() => setBrowse(true)}>Browse / upload {label}</button>{browse && <FileBrowser title={label} onSelect={path => { onChange(path); setBrowse(false); }} onCancel={() => setBrowse(false)} />}</div>;
}
function NativeList({ label, value, onChange, choices, separator = ' ' }: { label: string; value: string | number | undefined; onChange: (v: string) => void; choices?: string[]; separator?: string }) {
    const tokens = value === undefined || value === '' ? [] : String(value).trim().split(separator === ',' ? /\s*,\s*/ : /\s+/);
    const update = (i: number, text: string) => onChange(tokens.map((v, j) => j === i ? text : v).join(separator));
    return <fieldset><legend className="text-xs text-slate-300">{label} values</legend>
        {tokens.map((token, i) => <div key={i} className="flex gap-2">{choices
            ? <label>{label} {i + 1}<select aria-label={`${label} ${i + 1}`} className={inputClass} value={token} onChange={e => update(i, e.target.value)}>{Array.from(new Set([...choices, token])).map(v => <option key={v}>{v}</option>)}</select></label>
            : <Text label={`${label} ${i + 1}`} value={token} numeric onChange={text => update(i, text)} />}
            <button type="button" aria-label={`Remove ${label} ${i + 1}`} onClick={() => onChange(tokens.filter((_, j) => j !== i).join(separator))}>Remove</button></div>)}
        <button type="button" className={buttonClass} onClick={() => onChange([...tokens, choices?.[0] ?? '0'].join(separator))}>Add {label} value</button>
        <button type="button" className={buttonClass} onClick={() => onChange('')}>Clear {label}</button>
    </fieldset>;
}
function MdpFields({ keys, value, onChange, prefix = '' }: { keys: string[]; value: NativeMdp; onChange: (m: NativeMdp) => void; prefix?: string }) {
    return <div className="grid gap-3 md:grid-cols-3">{keys.map(key => {
        const actualKey = Object.keys(value).find(candidate => canonicalKey(candidate) === canonicalKey(key)) ?? key;
        const update = (text: string) => onChange({ ...value, [actualKey]: text });
        const unset = () => { const next = { ...value }; delete next[actualKey]; onChange(next); };
        const currentValue = value[actualKey];
        const metadata = parameterFor(key);
        const choices = metadata?.choices.length ? metadata.choices : undefined;
        const control = metadata?.type.endsWith('-list') ? <NativeList label={`${prefix}${key}`} value={currentValue} onChange={update} choices={choices} separator={metadata.list_separator} /> : choices ? <label key={key} className="block text-xs text-slate-300">{prefix}{key}<select aria-label={`${prefix}${key}`} className={inputClass} value={currentValue ?? ''} onChange={e => e.target.value === "" ? unset() : update(e.target.value)}><option value="">Unspecified (native)</option>{Array.from(new Set([...choices, ...(currentValue === undefined ? [] : [String(currentValue)])])).map(v => <option key={v} value={v}>{v}</option>)}</select></label>
            : <Text key={key} label={`${prefix}${key}`} value={currentValue} numeric={metadata?.type === 'number'} onChange={update} />;
        return <div key={key}>{control}{metadata && <details className="text-xs text-slate-400"><summary>{metadata.unit ? `${metadata.unit} · ` : ''}{metadata.authority} · native default: {metadata.native_default ?? 'see manual'}</summary><p>{metadata.description}</p><p>{metadata.applicability}</p><a href={metadata.documentation} target="_blank" rel="noreferrer">GROMACS 2025.3 definition and applicability</a></details>}<button type="button" className="text-xs text-slate-400" aria-label={`Unset ${prefix}${key}`} onClick={unset}>Use native default (unset)</button></div>;
    })}</div>;
}
function MdpEditor({ value, onChange }: { value: NativeMdp; onChange: (m: NativeMdp) => void }) {
    const [text, setText] = useState<string | null>(null);
    const [error, setError] = useState('');
    return <details><summary>Advanced native MDP / import</summary><p className="text-xs text-slate-400">Apply replaces this stage map. Typed controls edit the same entries; unknown native keys are retained.</p><label>Import MDP<input type="file" accept=".mdp" onChange={async e => { const file = e.target.files?.[0]; if (file) { const source = await file.text(); try { onChange(parseNativeMdp(source)); setText(null); setError(''); } catch (err) { setError(String(err)); } } }} /></label><textarea aria-label="Native MDP" className={`${inputClass} min-h-40 font-mono`} value={text ?? renderNativeMdp(value)} onChange={e => setText(e.target.value)} /><button type="button" className={buttonClass} onClick={() => { try { onChange(parseNativeMdp(text ?? renderNativeMdp(value))); setText(null); setError(''); } catch (err) { setError(String(err)); } }}>Apply MDP</button><button type="button" className={buttonClass} onClick={() => { setText(null); setError(''); }}>Discard text edits</button>{error && <p role="alert">{error}</p>}</details>;
}
function AnalysisSettings({ value, onChange }: { value: MolecularDynamicsAnalysisDraft; onChange: (a: MolecularDynamicsAnalysisDraft) => void }) {
    const update = <K extends keyof MolecularDynamicsAnalysisDraft>(key: K, v: MolecularDynamicsAnalysisDraft[K]) => onChange({ ...value, [key]: v });
    return <div className="space-y-3"><label>Structural analysis<select aria-label="Structural analysis" className={inputClass} value={value.selection === undefined ? 'default' : value.selection === null ? 'none' : 'explicit'} onChange={e => { const next = { ...value }; if (e.target.value === 'default') delete next.selection; else next.selection = e.target.value === 'none' ? null : ''; onChange(next); }}><option value="default">Protein / backbone when present</option><option value="none">No structural fit</option><option value="explicit">Explicit MDAnalysis selection</option></select></label>{typeof value.selection === 'string' && <Text label="Structural selection" value={value.selection} onChange={s => update('selection', s)} />}
        <h4>Contacts (Å)</h4>{value.contacts?.map((row, i) => <div className="grid gap-2 md:grid-cols-4" key={i}>{(['name', 'selection_a', 'selection_b', 'cutoff_angstrom'] as const).map(key => <Text key={key} label={`Contact ${i + 1} ${key}`} numeric={key === 'cutoff_angstrom'} value={row[key]} onChange={s => update('contacts', value.contacts!.map((r, j) => j === i ? { ...r, [key]: s } : r))} />)}<button type="button" onClick={() => update('contacts', value.contacts!.filter((_, j) => j !== i))}>Remove contact</button></div>)}<button type="button" className={buttonClass} onClick={() => update('contacts', [...(value.contacts ?? []), { name: '', selection_a: '', selection_b: '', cutoff_angstrom: '' }])}>Add contact</button>
        <h4>Pull traces: explicit emitted XVG columns</h4>{value.pull_coordinates?.map((row, i) => <div className="grid gap-2 md:grid-cols-3" key={i}>{(['coordinate', 'column', 'label', 'unit', 'groups', 'window'] as const).map(key => <Text key={key} label={`Trace ${i + 1} ${key}`} numeric={key === 'coordinate' || key === 'column'} value={key === 'groups' ? row.groups.join(' ') : row[key] ?? ''} onChange={s => update('pull_coordinates', value.pull_coordinates!.map((r, j) => j === i ? { ...r, [key]: key === 'groups' ? s.split(/\s+/).filter(Boolean) : ['coordinate', 'column'].includes(key) ? s : key === 'window' && s === '' ? null : s } : r))} />)}<button type="button" onClick={() => update('pull_coordinates', value.pull_coordinates!.filter((_, j) => j !== i))}>Remove trace</button></div>)}<button type="button" className={buttonClass} onClick={() => update('pull_coordinates', [...(value.pull_coordinates ?? []), { coordinate: '', column: '', label: '', unit: '', groups: [] }])}>Add pull trace</button>
        <label className="block"><input type="checkbox" checked={value.wham != null} onChange={e => update('wham', e.target.checked ? { unit: '', temperature_k: '', windows: [] } : null)} /> Native 1D WHAM across selected windows (not joint 2D PMF)</label>{value.wham && <><div className="grid gap-3 md:grid-cols-3">{(['unit', 'temperature_k', 'bins', 'begin_ps', 'end_ps'] as const).map(key => <Text key={key} label={`WHAM ${key}`} numeric={key !== 'unit'} value={value.wham![key] ?? ''} onChange={s => { const next = { ...value.wham! }; if (s === '' && key === 'end_ps') next.end_ps = null; else if (s === '' && (key === 'bins' || key === 'begin_ps')) delete next[key]; else Object.assign(next, { [key]: s }); update('wham', next); }} />)}</div>{value.wham.windows.map((row, i) => <div key={i} className="grid gap-2 md:grid-cols-4">{(['replica', 'window', 'coordinate', 'coordinate_count'] as const).map(key => <Text key={key} label={`WHAM window ${i + 1} ${key}`} numeric={key !== 'window'} value={row[key]} onChange={s => update('wham', { ...value.wham!, windows: value.wham!.windows.map((r, j) => j === i ? { ...r, [key]: s } : r) })} />)}<button type="button" onClick={() => update('wham', { ...value.wham!, windows: value.wham!.windows.filter((_, j) => j !== i) })}>Remove WHAM window</button></div>)}<button type="button" className={buttonClass} onClick={() => update('wham', { ...value.wham!, windows: [...value.wham!.windows, { replica: '', window: '', coordinate: '', coordinate_count: '' }] })}>Add WHAM window</button></>}
    </div>;
}
export function MolecularDynamicsNativeSettings({ value, onChange }: { value: MolecularDynamicsNativeDraft; onChange: (v: MolecularDynamicsNativeDraft) => void }) {
    const [uploadError, setUploadError] = useState('');
    const [uploadedTopologyPaths, setUploadedTopologyPaths] = useState<string[]>([]);
    const changeInput = (key: string, text: string) => { const input = { ...value.input }; if (text === '') delete (input as Record<string, unknown>)[key]; else Object.assign(input, { [key]: text }); onChange({ ...value, input }); };
    return <section className="space-y-5 rounded-2xl border border-slate-700 bg-slate-900/55 p-5" aria-label="Native GROMACS settings">
        <h2 className="font-semibold">Native GROMACS protocol</h2><p className="text-xs text-slate-400">{nativeParameterCatalog.runtime_reconciliation.optional_build_features}</p><p className="text-xs text-slate-400">Supplied systems keep their coordinates, box and topology. Unset MDP entries are left to native GROMACS. No preparation or physics substitution is applied.</p>
        <div className="grid gap-3 md:grid-cols-2">{(value.input.kind === 'compiled' ? ['tpr', 'checkpoint'] : value.input.kind === 'prepared' ? ['coordinates', 'topology', 'index', 'restraint_reference', 'checkpoint'] : ['index', 'restraint_reference']).map(key => <MdManagedFile key={key} label={key} value={(value.input as unknown as Record<string, string>)[key]} onChange={s => changeInput(key, s)} />)}</div>
        {value.input.kind === 'prepared' && <div><label>Upload topology include tree<input aria-label="Upload topology include tree" type="file" multiple {...{ webkitdirectory: '' }} onChange={async e => { const files = Array.from(e.target.files ?? []); const root = `inputs/md/${crypto.randomUUID()}`; try { const paths: string[] = []; for (const file of files) { const relative = file.webkitRelativePath || file.name; const folder = relative.includes('/') ? relative.slice(0, relative.lastIndexOf('/')) : ''; const uploaded = await uploadFile(`${root}${folder ? `/${folder}` : ''}`, file); if (file.name.endsWith('.top')) paths.push(uploaded.data.path); } setUploadedTopologyPaths(paths); setUploadError(''); } catch (err) { setUploadError(String(err)); } }} /></label><p className="text-xs">Relative names are preserved in a new managed folder. Select the root topology below, or browse the managed folder.</p>{uploadedTopologyPaths.map(path => <button key={path} type="button" className={buttonClass} onClick={() => changeInput('topology', path)}>Use topology {path}</button>)}{uploadError && <p role="alert">{uploadError}</p>}</div>}
        {value.input.kind === 'guided' && <div className="grid gap-3 md:grid-cols-3">{(['box_type', 'padding_nm', 'salt_molar'] as const).map(key => <Text key={key} label={`Preparation ${key}`} numeric={key !== 'box_type'} value={value.input[key]} onChange={s => onChange({ ...value, input: { ...value.input, [key]: s } })} />)}<label><input type="checkbox" checked={value.input.neutralize ?? true} onChange={e => onChange({ ...value, input: { ...value.input, neutralize: e.target.checked } })} /> Neutralize system</label></div>}
        <div className="grid gap-3 md:grid-cols-3"><Text label="Replicas per window" numeric value={value.replicas} onChange={s => onChange({ ...value, replicas: s })} /><Text label="Orchestration seed (not native stochastic seed)" numeric value={value.random_seed} onChange={s => onChange({ ...value, random_seed: s })} /><label>ntmpi (fixed)<input aria-label="ntmpi" className={inputClass} value={value.execution.ntmpi} readOnly /></label><label>ntomp<input aria-label="ntomp" className={inputClass} type="number" min={1} max={128} step={1} value={value.execution.ntomp} onChange={e => onChange({ ...value, execution: { ...value.execution, ntomp: e.target.value } })} /></label>{(['gpu_offload', 'pin'] as const).map(key => <label key={key}>{key}<select aria-label={key} className={inputClass} value={value.execution[key]} onChange={e => onChange({ ...value, execution: { ...value.execution, [key]: e.target.value } })}>{(key === 'pin' ? ['on', 'off', 'auto'] : ['full', 'full_forces', 'auto', 'none']).map(choice => <option key={choice}>{choice}</option>)}</select></label>)}</div>
        {value.input.kind === 'compiled' ? <p>TPR controls the compiled physics. MDP editing is not applied to this input; optional CPT resumes its native state.</p> : <>
            {value.stages.map((stage, i) => {
                const change = (mdp: NativeMdp) => onChange({ ...value, stages: value.stages.map((s, j) => j === i ? { ...s, mdp } : s) });
                const pullCount = Math.max(Number(stage.mdp['pull-ncoords']) || 0, ...Object.keys(stage.mdp).map(k => Number(k.match(/^pull-coord(\d+)-/)?.[1]) || 0));
                const groupCount = Math.max(Number(stage.mdp['pull-ngroups']) || 0, ...Object.keys(stage.mdp).map(k => Number(k.match(/^pull-group(\d+)-/)?.[1]) || 0));
                return <div key={i} className="space-y-3 rounded border border-slate-700 p-4"><Text label={`Stage ${i + 1} name`} value={stage.name} onChange={name => {
                    const windows = value.windows?.map(w => {
                        if (!w.mdp || !(stage.name in w.mdp)) return w;
                        const mdp = { ...w.mdp, [name]: w.mdp[stage.name] }; if (name !== stage.name) delete mdp[stage.name]; return { ...w, mdp };
                    });
                    onChange({ ...value, stages: value.stages.map((s, j) => j === i ? { ...s, name } : s), ...(windows === undefined ? {} : { windows }) });
                }} /><Text label={`Stage ${i + 1} checkpoint minutes`} numeric value={stage.checkpoint_interval_minutes} onChange={s => { const next = { ...stage }; if (s === '') delete next.checkpoint_interval_minutes; else next.checkpoint_interval_minutes = s; onChange({ ...value, stages: value.stages.map((v, j) => j === i ? next : v) }); }} />
                    <div className="flex gap-2"><button type="button" disabled={i === 0} onClick={() => { const stages = [...value.stages]; [stages[i - 1], stages[i]] = [stages[i], stages[i - 1]]; onChange({ ...value, stages }); }}>Move stage up</button><button type="button" onClick={() => onChange({ ...value, stages: value.stages.filter((_, j) => j !== i) })}>Remove stage</button></div>
                    {Object.entries(sections).map(([title, keys], j) => <details key={title} open={j === 0}><summary>{title}</summary><MdpFields keys={nativeSectionKeys(keys, stage.mdp)} value={stage.mdp} onChange={change} /></details>)}
                    <details><summary>Pull groups and simultaneous coordinates</summary><MdpFields keys={nativeParameterCatalog.parameters.filter(row => row.section === 'COM pulling' && !row.index_family).map(row => row.key)} value={stage.mdp} onChange={change} />{Array.from({ length: groupCount }, (_, n) => <MdpFields key={n} keys={nativeParameterCatalog.parameters.filter(row => row.index_family === 'pull-group').map(row => row.key.replace('group1-', `group${n + 1}-`))} value={stage.mdp} onChange={change} />)}<button type="button" className={buttonClass} onClick={() => change({ ...stage.mdp, 'pull-ngroups': groupCount + 1 })}>Add pull group</button>{Array.from({ length: pullCount }, (_, n) => <MdpFields key={n} keys={nativeParameterCatalog.parameters.filter(row => row.index_family === 'pull-coord').map(row => row.key.replace('coord1-', `coord${n + 1}-`))} value={stage.mdp} onChange={change} />)}<button type="button" className={buttonClass} onClick={() => change({ ...stage.mdp, 'pull-ncoords': pullCount + 1 })}>Add pull coordinate</button></details>
                    <MdpEditor key={stage.name} value={stage.mdp} onChange={change} />
                </div>;
            })}<button type="button" className={buttonClass} onClick={() => onChange({ ...value, stages: [...value.stages, { name: `stage${value.stages.length + 1}`, mdp: {} }] })}>Add ordered stage</button>
        </>}
        <details><summary>Windows: explicit per-stage center / stiffness changes</summary><p className="text-xs">Unchanged values inherit from the named stage. Each replica repeats a window; native seed entries remain unchanged.</p>{value.windows?.map((window, i) => <div key={i} className="space-y-3 border-b border-slate-700 p-3"><Text label={`Window ${i + 1} ID`} value={window.id} onChange={id => onChange({ ...value, windows: value.windows!.map((w, j) => j === i ? { ...w, id } : w) })} />{value.input.kind === 'compiled' ? <p className="text-xs">Coordinates are defined by the compiled TPR; window coordinates cannot change it.{window.coordinates && <> Retained coordinates: {window.coordinates}</>}</p> : <MdManagedFile label={`Window ${i + 1} coordinates`} value={window.coordinates} onChange={coordinates => onChange({ ...value, windows: value.windows!.map((w, j) => j === i ? { ...w, coordinates } : w) })} />}{[...value.stages, ...Object.keys(window.mdp ?? {}).filter(name => !value.stages.some(stage => stage.name === name)).map(name => ({ name, mdp: {} as NativeMdp }))].map(stage => {
            const overrides = window.mdp?.[stage.name] ?? {};
            const keys = Array.from(new Set([...Object.keys(stage.mdp).filter(k => /^pull-coord\d+-(init|k|kB)$/.test(k)), ...Object.keys(overrides)]));
            const change = (mdp: NativeMdp) => onChange({ ...value, windows: value.windows!.map((w, j) => j === i ? { ...w, mdp: { ...w.mdp, [stage.name]: mdp } } : w) });
            return <div key={stage.name}><h4>{stage.name}</h4><MdpFields keys={keys} prefix={`${window.id} ${stage.name} `} value={overrides} onChange={change} /><p className="text-xs">Effective: {keys.map(k => `${k} = ${overrides[k] ?? stage.mdp[k]}`).join(' · ')}</p><MdpEditor value={overrides} onChange={change} /></div>;
        })}<button type="button" onClick={() => onChange({ ...value, windows: value.windows!.filter((_, j) => j !== i) })}>Remove window</button></div>)}<button type="button" className={buttonClass} onClick={() => onChange({ ...value, windows: [...(value.windows ?? []), { id: `w${value.windows?.length ?? 0}` }] })}>Add window</button></details>
        <details><summary>Optional analysis</summary><p className="text-xs">Missing or failed optional analysis does not change completed dynamics. Selections and units are explicit.</p><AnalysisSettings value={value.analysis ?? {}} onChange={analysis => onChange({ ...value, analysis })} /></details>
    </section>;
}
