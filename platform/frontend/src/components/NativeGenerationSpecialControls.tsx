import { useEffect, useRef, useState } from 'react';
import { FrameworkBrowser } from './FrameworkBrowser';
import { SequenceManagerModal } from './SequenceManagerModal';
import { uploadFile } from '../lib/api';

const input = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 text-sm';
const button = 'rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm';

export function NativeSequenceTemplate({ name, value, onChange }: { name: string; value: unknown; onChange: (value: string) => void }) {
    const [library, setLibrary] = useState(false);
    return <div className="space-y-2"><textarea className={`${input} font-mono`} aria-label={name} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} rows={3} />
        <button type="button" className={button} onClick={() => setLibrary(true)}>Sequence library</button>
        <p className="text-xs text-[var(--text-secondary)]">Native sequence/length template, preserved as entered. Sequence-only input has no coordinates.</p>
        <SequenceManagerModal isOpen={library} onClose={() => setLibrary(false)} onSelect={sequence => { onChange(sequence.sequence); setLibrary(false); }} />
    </div>;
}
export function NativeDatasetInput({ name, value, onChange, onBrowse }: { name: string; value: unknown; onChange: (value: string) => void; onBrowse?: () => void }) {
    const [error, setError] = useState('');
    const epoch = useRef(0);
    useEffect(() => { epoch.current++; return () => { epoch.current++; }; }, [value]);
    return <div className="space-y-2"><input className={input} aria-label={name} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} />
        <div className="flex flex-wrap gap-2">{onBrowse && <button type="button" className={button} onClick={onBrowse}>Browse managed dataset / native input</button>}
            <label className={button}>Upload native input<input className="block text-xs" aria-label={`Upload ${name}`} type="file" onChange={async event => {
                const file = event.target.files?.[0]; if (!file) return;
                const token = ++epoch.current;
                try { setError(''); const uploaded = await uploadFile('inputs', new File([file], `${crypto.randomUUID()}-${file.name}`, { type: file.type })); if (epoch.current === token) onChange(uploaded.data.path); }
                catch (error) { if (epoch.current === token) setError(error instanceof Error ? error.message : String(error)); }
            }} /></label></div>
        {error && <p role="alert">{error}</p>}
    </div>;
}
export function NativeFrameworkIdentity({ value, onChange }: { value: unknown; onChange: (value: unknown) => void }) {
    const [open, setOpen] = useState(false);
    const identity = value && typeof value === 'object' ? value as Record<string, unknown> : {};
    return <div className="space-y-2"><button type="button" className={button} onClick={() => setOpen(!open)}>Choose SAbDab framework identity</button>
        {open && <FrameworkBrowser onSelect={framework => { if (framework) { onChange({ ...framework }); setOpen(false); } }} />}
        {Object.keys(identity).length > 0 && <dl className="text-xs">{Object.entries(identity).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value)).map(([key, value]) => <div key={key}><dt className="font-medium">{key}</dt><dd className="break-all">{String(value)}</dd></div>)}</dl>}
        <p className="text-xs text-[var(--text-secondary)]">The selected identity is retained unchanged. Native scaffold preparation remains server-owned.</p>
    </div>;
}

/** Exact six atom fields consumed by prep_boltzgen.py; no spatial inference. */
export function NativeCovalentBonds({ value, onChange }: { value: unknown; onChange: (value: string) => void }) {
    let rows: Record<string, unknown>[] = []; let error = '';
    try {
        const parsed = typeof value === 'string' && value ? JSON.parse(value) : value ?? [];
        if (!Array.isArray(parsed) || parsed.some(row => !row || typeof row !== 'object' || Array.isArray(row))) throw new Error('Expected a native bond list');
        rows = parsed;
    } catch { error = 'Saved covalent-bond input is not a native list. It is retained unchanged; import a supported native list to edit its atom fields.'; }
    const commit = (next: Record<string, unknown>[]) => onChange(JSON.stringify(next));
    return <div className="space-y-3">{error ? <p role="alert">{error}</p> : <>
        {rows.map((row, index) => <fieldset className="space-y-2 rounded border border-[var(--border-primary)] p-3" key={index}><legend>Bond {index + 1}</legend>
            <label className="text-xs">Native bond type<input className={input} aria-label={`Bond ${index + 1} type`} value={String(row.type ?? '')} onChange={event => commit(rows.map((entry, i) => i === index ? { ...entry, type: event.target.value } : entry))} /></label>
            <div className="grid gap-2 sm:grid-cols-2">{[1, 2].map(atom => <div className="space-y-2" key={atom}><h4 className="text-sm">Atom {atom}</h4>{['chain', 'residue', 'atom'].map(field => {
                const key = `atom${atom}_${field}`;
                return <label className="block text-xs" key={key}>{field}<input className={input} aria-label={`Bond ${index + 1} ${key}`} type={field === 'residue' ? 'number' : 'text'} value={String(row[key] ?? '')} onChange={event => commit(rows.map((entry, i) => i === index ? { ...entry, [key]: field === 'residue' && event.target.value !== '' ? Number(event.target.value) : event.target.value } : entry))} /></label>;
            })}</div>)}</div><button type="button" className={button} onClick={() => commit(rows.filter((_, i) => i !== index))}>Remove bond</button>
        </fieldset>)}<button type="button" className={button} onClick={() => commit([...rows, { type: '', atom1_chain: '', atom1_residue: '', atom1_atom: '', atom2_chain: '', atom2_residue: '', atom2_atom: '' }])}>Add native bond</button>
    </>}</div>;
}
