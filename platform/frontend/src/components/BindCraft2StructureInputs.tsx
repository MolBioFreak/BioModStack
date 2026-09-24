import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { BC2Inventory, BC2Request } from './BindCraft2Settings';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { EpitopeSelector } from './EpitopeSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { downloadSabdabFramework, fetchFiles, getSabdabAttribution, getSabdabFilterOptions, listCachedFrameworks, searchSabdabFrameworks } from '../lib/api';
import { acquireBC2Source, bc2Targets, editVisualResidues, fastaChains, hasInitialScaffoldSlot, hasInitialTargetSlot, parseBC2Document, readBC2Source, replaceTargetSource, residueKey, selectedChains, textValue, visualResidues, type BC2Document, type BC2InitialSources, type BC2Source, type BC2Target } from '../lib/bindcraft2StructureInputs';
import type { Chain } from '../utils/pdbUtils';

const surface = 'rounded-2xl border border-[var(--border-color)] bg-[var(--bg-secondary)]';
const button = 'rounded-lg border border-[var(--border-color)] px-3 py-2 text-sm transition-colors hover:border-accent hover:text-accent disabled:opacity-50';
const input = 'min-w-0 w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] px-3 py-2 text-sm';
const small = 'text-xs text-[var(--text-secondary)]';
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Discovery uses the same governed framework APIs as FrameworkBrowser, but does
 * not carry its antibody-only presets, CDR edits or implicit chain assumptions. */
function ScaffoldLibrary({ onSelect }: { onSelect: (source: BC2Source) => void }) {
    const [species, setSpecies] = useState('');
    const [resolution, setResolution] = useState('');
    const [page, setPage] = useState(0);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState('');
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const filters = useQuery({ queryKey: ['sabdab-filter-options'], queryFn: getSabdabFilterOptions });
    const cached = useQuery({ queryKey: ['cached-frameworks'], queryFn: listCachedFrameworks });
    const attribution = useQuery({ queryKey: ['sabdab-attribution'], queryFn: getSabdabAttribution });
    const search = useQuery({ queryKey: ['bc2-scaffold-search', species, resolution, page], queryFn: () => searchSabdabFrameworks({ species: species || undefined, resolution_max: resolution ? Number(resolution) : undefined, offset: page * 20, limit: 20, sort_by: 'resolution' }) });
    const download = async (code: string) => {
        setBusy(code); setError('');
        try {
            const { data } = await downloadSabdabFramework(code, { scheme: 'imgt', convert_hlt: true, include_content: true });
            if (!alive.current) return;
            if (!data.file_path && !data.pdb_content) throw new Error('The framework download returned no structure. Retry the download.');
            onSelect(data.file_path ? { path: data.file_path, name: `${code}.pdb` } : { file: new File([data.pdb_content || ''], `${code}.pdb`), name: `${code}.pdb` });
        } catch (error) { if (alive.current) setError(message(error)); }
        finally { if (alive.current) setBusy(''); }
    };
    return <div className="space-y-4">
        <div><h4 className="font-medium">Antibody scaffold library</h4><p className={small}>SAbDab discovery. Inspect the downloaded chains before choosing a subset; custom sources remain available for every binder type.</p></div>
        <div className="grid gap-3 sm:grid-cols-2"><label className={small}>Species<select className={input} aria-label="Scaffold species" value={species} onChange={event => { setSpecies(event.currentTarget.value); setPage(0); }}><option value="">All species</option>{filters.data?.data.species.map(name => <option key={name}>{name}</option>)}</select></label>
            <label className={small}>Maximum resolution (Å)<input className={input} type="number" step="any" aria-label="Scaffold resolution" value={resolution} onChange={event => { setResolution(event.currentTarget.value); setPage(0); }} /></label></div>
        {!!cached.data?.data.frameworks.length && <details><summary className="cursor-pointer text-sm">Previously downloaded frameworks</summary><div className="mt-2 flex flex-wrap gap-2">{cached.data.data.frameworks.map(row => <button type="button" className={button} key={`${row.pdb_code}:${row.scheme}`} onClick={() => onSelect({ path: row.file_path, name: `${row.pdb_code}.pdb` })}>{row.pdb_code} · {row.scheme}</button>)}</div></details>}
        <div className="max-h-72 space-y-2 overflow-auto" aria-label="Scaffold library results">{search.isFetching && <p className={small}>Searching frameworks…</p>}{search.data?.data.results.map(row => <button type="button" className={`${button} flex w-full items-center justify-between gap-3 text-left`} disabled={!!busy} key={`${row.pdb_code}:${row.h_chain}:${row.model}`} onClick={() => void download(row.pdb_code)}><span><strong>{row.pdb_code}</strong> <span className={small}>{row.species || 'Species not recorded'}</span></span><span className={small}>{busy === row.pdb_code ? 'Loading…' : `${row.resolution ?? '—'} Å · ${row.h_chain}`}</span></button>)}</div>
        <div className="flex items-center justify-between gap-2"><button type="button" className={button} disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span className={small}>{search.data?.data.total ?? 0} frameworks</span><button type="button" className={button} disabled={!search.data || (page + 1) * 20 >= search.data.data.total} onClick={() => setPage(page + 1)}>Next</button></div>
        {(error || search.error) && <p role="alert" className="text-sm text-red-400">{error || message(search.error)}</p>}
        {attribution.data && <p className={small}>{attribution.data.data.citation} · {attribution.data.data.license}</p>}
    </div>;
}

function SourceChooser({ scaffold, onSelect, onClose }: { scaffold: boolean; onSelect: (source: BC2Source) => void; onClose: () => void }) {
    const [tab, setTab] = useState<'file' | 'catalog' | 'library'>('file');
    const [folder, setFolder] = useState('/');
    const [entries, setEntries] = useState<Array<{ path: string; name: string; is_directory: boolean }> | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const epoch = useRef(0);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; epoch.current++; }; }, []);
    const choose = (source: BC2Source) => { if (alive.current) { alive.current = false; onSelect(source); } };
    const browse = async (path: string) => {
        const token = ++epoch.current; setBusy(true); setError('');
        try { const response = await fetchFiles(path); if (alive.current && token === epoch.current) { setEntries(response.data.entries); setFolder(path); } }
        catch (error) { if (alive.current && token === epoch.current) setError(message(error)); }
        finally { if (alive.current && token === epoch.current) setBusy(false); }
    };
    return <div className={`${surface} space-y-4 p-4`} aria-label={scaffold ? 'Choose scaffold source' : 'Choose target source'}>
        <div className="flex flex-wrap justify-between gap-2"><div className="flex flex-wrap gap-1" role="tablist" aria-label="Source locations">{([['file', 'Upload / files'], ['catalog', 'Runs / presets / RCSB'], ...(scaffold ? [['library', 'Antibody library']] : [])] as const).map(([key, label]) => <button type="button" role="tab" aria-selected={tab === key} key={key} className={`${button} ${tab === key ? 'border-accent bg-accent/10 text-accent' : ''}`} onClick={() => setTab(key as typeof tab)}>{label}</button>)}</div><button className={button} type="button" onClick={onClose}>Close</button></div>
        {tab === 'file' && <div className="space-y-3"><label className="block cursor-pointer rounded-xl border border-dashed border-accent/40 bg-accent/5 p-5"><span className="block font-medium">Choose {scaffold ? 'a scaffold structure' : 'a structure or FASTA'}</span><span className={`${small} mb-3 block`}>{scaffold ? 'PDB · mmCIF' : 'PDB · mmCIF · FASTA'} · bytes are stored through the governed files service</span><input className="block w-full text-sm" aria-label={scaffold ? 'Upload scaffold source' : 'Upload target source'} type="file" accept={scaffold ? '.pdb,.cif,.mmcif' : '.pdb,.cif,.mmcif,.fa,.fasta,.faa'} onChange={event => { const file = event.currentTarget.files?.[0]; if (file) choose({ file, name: file.name }); }} /></label>
            <button className={button} type="button" disabled={busy} onClick={() => void browse(folder)}>Browse managed files</button>
            {entries && <div className="max-h-64 overflow-auto rounded-lg border border-[var(--border-color)] p-2"><div className="flex items-center gap-2"><button type="button" className={button} disabled={folder === '/'} onClick={() => void browse('/' + folder.split('/').filter(Boolean).slice(0, -1).join('/'))}>Up one folder</button><span className={`${small} break-all`}>{folder}</span></div>{entries.filter(entry => entry.is_directory || (scaffold ? /\.(pdb|cif|mmcif)$/i : /\.(pdb|cif|mmcif|fa|fasta|faa)$/i).test(entry.path)).map(entry => <button type="button" className="block w-full rounded p-2 text-left text-sm hover:bg-accent/10" key={entry.path} onClick={() => entry.is_directory ? void browse(entry.path) : choose({ path: entry.path, name: entry.name })}>{entry.is_directory ? '▸ ' : ''}{entry.name}</button>)}</div>}
        </div>}
        {tab === 'catalog' && <TargetAntigenSelector key="catalog" label={scaffold ? 'Scaffold structure' : 'Target structure'} initialTab="runs" onSelect={source => { if (source) choose(source); }} />}
        {tab === 'library' && <ScaffoldLibrary onSelect={choose} />}
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    </div>;
}

type Cache = Map<string, BC2Document>;
function useSourceDocument(path: string, cache: Cache) {
    const [loaded, setLoaded] = useState<{ path: string; document: BC2Document }>();
    const [failure, setFailure] = useState<{ path: string; error: string }>();
    const [retry, setRetry] = useState(0);
    useEffect(() => {
        if (!path || cache.has(path)) return;
        const controller = new AbortController();
        void readBC2Source({ path, name: path }, controller.signal).then(content => parseBC2Document(content, path)).then(document => {
            if (!controller.signal.aborted) { cache.set(path, document); setLoaded({ path, document }); }
        }).catch(error => { if (!controller.signal.aborted) setFailure({ path, error: message(error) }); });
        return () => controller.abort();
    }, [path, cache, retry]);
    return { document: cache.get(path) || (loaded?.path === path ? loaded.document : undefined), error: failure?.path === path ? failure.error : '', retry: () => { setFailure(undefined); setRetry(old => old + 1); } };
}

function NativeTargetFields({ target, index, update }: { target: BC2Target; index: number; update: (field: string, next: unknown) => void }) {
    return <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_7rem]">
        <label className={small}>Name<input className={input} aria-label={`targets.${index}.name`} value={textValue(target.name)} onChange={event => update('name', event.currentTarget.value)} /></label>
        <label className={small}>Objective<select className={input} aria-label={`targets.${index}.objective`} value={textValue(target.objective)} onChange={event => update('objective', event.currentTarget.value || undefined)}><option value="">Native default</option><option value="target">Target</option><option value="detarget">Detarget</option>{!!target.objective && !['target', 'detarget'].includes(String(target.objective)) && <option value={String(target.objective)}>{String(target.objective)}</option>}</select></label>
        <label className={small}>Weight<input className={input} type="number" step="any" placeholder="1 (native)" aria-label={`targets.${index}.weight`} value={typeof target.weight === 'number' ? target.weight : ''} onChange={event => update('weight', event.currentTarget.value === '' ? undefined : Number(event.currentTarget.value))} /></label>
    </div>;
}

/** The canonical request is the only scientific state. Documents/cache are previews
 * keyed by exact materialized source; asynchronous work never owns the request. */
export function BindCraft2StructureInputs({ value, onChange, inventory, initialSources }: {
    value: BC2Request; onChange: (next: BC2Request) => void; inventory: BC2Inventory; initialSources?: BC2InitialSources;
}) {
    const latest = useRef(value); latest.current = value;
    const change = useRef(onChange); change.current = onChange;
    const cache = useRef<Cache>(new Map()).current;
    const closedInitialSlots = useRef(new Set<'target' | 'scaffold'>());
    if (!hasInitialTargetSlot(value)) closedInitialSlots.current.add('target');
    if (!hasInitialScaffoldSlot(value)) closedInitialSlots.current.add('scaffold');
    const modelFamilies = useRef(new Map<string, BC2Document>()).current;
    const selectedModels = useRef(new Map<string, number>()).current;
    const [active, setActive] = useState<number | 'scaffold'>(0);
    const [chooser, setChooser] = useState<number | 'scaffold' | null>(null);
    const [busy, setBusy] = useState<number | 'scaffold' | null>(null);
    const [error, setError] = useState('');
    const [mode, setMode] = useState<'hotspots' | 'coldspots'>('hotspots');
    const [view, setView] = useState<'structure' | 'sequence'>('structure');
    const sourceEpoch = useRef(0);
    const mounted = useRef(true);
    const emit = (next: BC2Request) => { latest.current = next; change.current(next); };
    const setTarget = (index: number, transform: (target: BC2Target) => BC2Target) => emit({ ...latest.current, targets: bc2Targets(latest.current).map((target, position) => position === index ? transform(target) : target) });
    const update = (index: number, field: string, next: unknown) => setTarget(index, target => { const copy = { ...target }; if (next === undefined) delete copy[field]; else copy[field] = next; return copy; });
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; sourceEpoch.current++; }; }, []);
    // Compatibility handoff is one-way and only into absent native slots. Any user
    // source gesture invalidates pending adoption, including a canceled chooser.
    useEffect(() => {
        if (!initialSources) return;
        let cancelled = false;
        const epoch = sourceEpoch.current;
        const adopt = async (role: 'target' | 'scaffold') => {
            const source = initialSources[role];
            if (!source || closedInitialSlots.current.has(role) || !(role === 'target' ? hasInitialTargetSlot(latest.current) : hasInitialScaffoldSlot(latest.current))) return;
            try {
                const content = role === 'target' ? initialSources.target?.modelPdb : initialSources.scaffold?.pdbContent;
                const prepared: BC2Source = content && (role === 'target' || !source.path && !source.file) ? { name: source.name, file: new File([content], `${source.name.replace(/\.(pdb|cif|mmcif)$/i, '')}.pdb`) } : source;
                const result = await acquireBC2Source(prepared);
                if (cancelled || !mounted.current || sourceEpoch.current !== epoch || closedInitialSlots.current.has(role)) return;
                const current = latest.current;
                if (role === 'target' && hasInitialTargetSlot(current)) {
                    cache.set(result.path, result.document);
                    const target = initialSources.target!;
                    emit({ ...current, targets: [{ name: target.name, target_path: result.path, ...(target.chains !== undefined ? { chains: target.chains } : {}), ...(target.hotspots !== undefined ? { hotspots: target.hotspots } : {}) }] });
                } else if (role === 'scaffold' && hasInitialScaffoldSlot(current)) { cache.set(result.path, result.document); emit({ ...current, binder_scaffold: result.path }); }
            } catch (error) { if (!cancelled && mounted.current && sourceEpoch.current === epoch) setError(message(error)); }
        };
        void adopt('target'); void adopt('scaffold');
        return () => { cancelled = true; };
    }, [initialSources?.target?.file, initialSources?.target?.path, initialSources?.target?.url, initialSources?.target?.name, initialSources?.target?.chains, initialSources?.target?.hotspots, initialSources?.target?.modelPdb, initialSources?.scaffold?.file, initialSources?.scaffold?.path, initialSources?.scaffold?.url, initialSources?.scaffold?.name, initialSources?.scaffold?.pdbContent, cache]);
    const targets = bc2Targets(value);
    const target = typeof active === 'number' ? targets[active] : undefined;
    const activePath = active === 'scaffold' ? textValue(value.binder_scaffold) : textValue(target?.target_path);
    const loaded = useSourceDocument(activePath, cache);
    const document = loaded.document;
    const modelFamily = modelFamilies.get(activePath) || document;
    const model = document?.models[0];
    const chains = useMemo(() => document?.format === 'fasta' ? fastaChains(document, textValue(target?.chains)) : model?.chains || [], [document, model, target?.chains]);
    const included = selectedChains(textValue(target?.chains), chains);
    const visibleChains = active === 'scaffold' ? chains : chains.filter(chain => included.includes(chain.id));
    const firstChain = included[0] ?? chains[0]?.id ?? 'A';
    const selected = visualResidues(textValue(target?.[mode]), visibleChains, firstChain);
    const [scaffoldChains, setScaffoldChains] = useState<{ path: string; ids: string[] }>();
    const chosenScaffoldChains = scaffoldChains?.path === activePath ? scaffoldChains.ids : chains.map(chain => chain.id);
    const selectSource = async (slot: number | 'scaffold', source: BC2Source, family?: BC2Document, modelNumber?: number) => {
        closedInitialSlots.current.add(slot === 'scaffold' ? 'scaffold' : 'target');
        const token = ++sourceEpoch.current;
        const oldPath = slot === 'scaffold' ? latest.current.binder_scaffold : bc2Targets(latest.current)[slot]?.target_path;
        setChooser(null); setBusy(slot); setError('');
        try {
            const result = await acquireBC2Source(source);
            if (!mounted.current || token !== sourceEpoch.current) return;
            const currentPath = slot === 'scaffold' ? latest.current.binder_scaffold : bc2Targets(latest.current)[slot]?.target_path;
            if (oldPath !== currentPath || slot !== 'scaffold' && !bc2Targets(latest.current)[slot]) return;
            cache.set(result.path, result.document);
            if (family) modelFamilies.set(result.path, family);
            if (modelNumber !== undefined) selectedModels.set(result.path, modelNumber);
            if (slot === 'scaffold') emit({ ...latest.current, binder_scaffold: result.path });
            else setTarget(slot, old => replaceTargetSource(old, result.path, source.name));
            setActive(slot);
        } catch (error) { if (mounted.current && token === sourceEpoch.current) setError(message(error)); }
        finally { if (mounted.current && token === sourceEpoch.current) setBusy(null); }
    };
    const openChooser = (slot: number | 'scaffold') => { closedInitialSlots.current.add(slot === 'scaffold' ? 'scaffold' : 'target'); sourceEpoch.current++; setBusy(null); setError(''); setActive(slot); setChooser(slot); };
    const applyResidues = (after: Set<string>) => {
        if (typeof active !== 'number' || !target) return;
        try { update(active, mode, editVisualResidues(textValue(target[mode]), selected, after, visibleChains, firstChain)); setError(''); }
        catch (error) { setError(message(error)); }
    };
    const useModel = (number: number) => {
        const chosen = modelFamily?.models.find(item => item.number === number);
        if (!chosen || !modelFamily) return;
        void selectSource(active, { name: `model-${number}.${modelFamily.format}`, file: new File([chosen.content], `model-${number}.${modelFamily.format}`) }, modelFamily, number);
    };
    const useScaffoldSubset = async () => {
        if (!document || active !== 'scaffold') return;
        const token = ++sourceEpoch.current; setBusy('scaffold'); setError('');
        try {
            const { sliceBC2Structure } = await import('../lib/bindcraft2StructureInputs');
            const sliced = await sliceBC2Structure(document, chosenScaffoldChains);
            if (token !== sourceEpoch.current || !mounted.current || latest.current.binder_scaffold !== activePath) return;
            await selectSource('scaffold', { file: new File([sliced], `scaffold-chains.${document.format}`), name: `scaffold-chains.${document.format}` });
        } catch (error) { if (token === sourceEpoch.current && mounted.current) { setError(message(error)); setBusy(null); } }
    };
    const profileScaffold = inventory.fields.binder_scaffold?.native_default;
    return <section aria-label="BindCraft2 structure preparation" className="min-w-0 space-y-5 text-[var(--text-primary)] [overflow-wrap:anywhere]" onClickCapture={event => { const element = event.target as HTMLElement; if (element.closest('button:not([type])')) event.preventDefault(); }}>
        <header className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-accent">Structure preparation</p><h3 className="mt-1 text-lg font-semibold">Targets & scaffold</h3><p className={`${small} mt-1`}>Prepare each source, inspect its chains, then mark target contact regions.</p></div><button type="button" className={`${button} bg-accent/10 text-accent`} onClick={() => { sourceEpoch.current++; setBusy(null); const next = bc2Targets(latest.current); emit({ ...latest.current, targets: [...next, { name: `target_${next.length + 1}`, target_path: '' }] }); setActive(next.length); setChooser(next.length); }}>+ Add target</button></header>
        <div className="grid min-w-0 gap-3 lg:grid-cols-2">
            {Object.hasOwn(value, 'targets') && <div className="flex items-center justify-between gap-3 lg:col-span-2"><p className={small}>Explicit target set · {targets.length} source{targets.length === 1 ? '' : 's'}</p><button type="button" className={button} onClick={() => { closedInitialSlots.current.add('target'); sourceEpoch.current++; setBusy(null); setChooser(null); const next = { ...latest.current }; delete next.targets; emit(next); setActive(0); }}>Use native target set</button></div>}
            {targets.map((row, index) => <article key={index} className={`${surface} min-w-0 space-y-3 p-4 ${active === index ? 'ring-1 ring-accent/60' : ''}`} aria-label={`Target ${index + 1}`}>
                <div className="flex items-center justify-between gap-2"><button type="button" className="text-left text-sm font-semibold hover:text-accent" aria-pressed={active === index} onClick={() => setActive(index)}><span className="mr-2 rounded-md bg-accent/10 px-2 py-1 text-accent">{index + 1}</span>{textValue(row.name) || 'Untitled target'}</button><button type="button" className="text-xs text-[var(--text-secondary)] hover:text-red-400" aria-label={`Remove target ${index + 1}`} onClick={() => { sourceEpoch.current++; setBusy(null); setChooser(null); emit({ ...latest.current, targets: bc2Targets(latest.current).filter((_, position) => position !== index) }); setActive(0); }}>Remove</button></div>
                <NativeTargetFields target={row} index={index} update={(field, next) => update(index, field, next)} />
                <output aria-label={`targets.${index}.target_path`} className={`${small} block break-all rounded-lg bg-[var(--bg-primary)] p-2`}>{textValue(row.target_path) || 'Choose a source to prepare this target'}</output>
                <div className="flex flex-wrap gap-2"><button type="button" className={button} onClick={() => openChooser(index)}>{row.target_path ? 'Replace source' : 'Choose source'}</button><button type="button" className={button} onClick={() => { setActive(index); setChooser(null); }}>Inspect & select residues</button></div>
                {Number(row.weight) < 0 && <p className={small}>Native negative weight selects a detarget objective.</p>}
            </article>)}
            {!targets.length && <div className={`${surface} flex min-h-40 flex-col items-start justify-center gap-2 border-dashed p-5`}><h4 className="font-medium">Build a target set</h4><p className={small}>{value.target ? 'A native target preset is selected in campaign settings. Add explicit sources here when you want to override its target set.' : 'Add one or several targets from files, prior runs, presets or RCSB.'}</p></div>}
            <article className={`${surface} min-w-0 space-y-3 p-4 ${active === 'scaffold' ? 'ring-1 ring-accent/60' : ''}`} aria-label="Binder scaffold"><div className="flex items-center justify-between gap-2"><button className="text-sm font-semibold hover:text-accent" type="button" aria-pressed={active === 'scaffold'} onClick={() => setActive('scaffold')}>Binder scaffold</button><span className={small}>Optional source override</span></div><p className={small}>Use a custom structure or discover an antibody framework. Generic binders keep their native scaffold semantics.</p><output aria-label="binder_scaffold" className={`${small} block break-all rounded-lg bg-[var(--bg-primary)] p-2`}>{textValue(value.binder_scaffold) || (profileScaffold ? `Native default: ${String(profileScaffold)}` : 'Native / selected modality default')}</output><div className="flex flex-wrap gap-2"><button type="button" className={button} onClick={() => openChooser('scaffold')}>Choose scaffold</button>{!!value.binder_scaffold && <button type="button" className={button} onClick={() => { setActive('scaffold'); setChooser(null); }}>Inspect scaffold</button>}{Object.hasOwn(value, 'binder_scaffold') && <button type="button" className={button} onClick={() => { sourceEpoch.current++; setBusy(null); setChooser(null); const next = { ...latest.current }; delete next.binder_scaffold; emit(next); }}>Use native default</button>}</div></article>
        </div>
        {chooser !== null && <SourceChooser key={chooser} scaffold={chooser === 'scaffold'} onClose={() => setChooser(null)} onSelect={source => void selectSource(chooser, source)} />}
        {busy !== null && <p role="status" className="text-sm text-accent">Preparing {busy === 'scaffold' ? 'scaffold' : `target ${busy + 1}`} source…</p>}
        {error && <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">{error}</p>}
        {(target || active === 'scaffold') && <div className={`${surface} min-w-0 overflow-hidden`}>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border-color)] p-4"><div><h4 className="font-semibold">{active === 'scaffold' ? 'Scaffold inspection' : textValue(target?.name) || 'Target inspection'}</h4><p className={small}>{document?.format === 'fasta' ? 'Sequence source · native FASTA record and positions' : 'Structure source · original chain IDs and residue numbering'}</p></div><div className="flex flex-wrap gap-2"><label className="sr-only" htmlFor="bc2-inspection-source">Inspect source</label><select id="bc2-inspection-source" className={`${input} !w-auto max-w-full`} value={active} onChange={event => setActive(event.currentTarget.value === 'scaffold' ? 'scaffold' : Number(event.currentTarget.value))}>{targets.map((row, index) => <option key={index} value={index}>Target: {textValue(row.name) || index + 1}</option>)}<option value="scaffold">Binder scaffold</option></select><button type="button" className={`${button} ${view === 'structure' ? 'text-accent' : ''}`} aria-pressed={view === 'structure'} onClick={() => setView('structure')}>3D + sequence</button><button type="button" className={`${button} ${view === 'sequence' ? 'text-accent' : ''}`} aria-pressed={view === 'sequence'} onClick={() => setView('sequence')}>Sequence</button></div></div>
            <div className="grid min-w-0 gap-5 p-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
                <div className="min-w-0 space-y-4">
                    {modelFamily && modelFamily.models.length > 1 && <label className={small}>Use a model from this source<select className={input} aria-label="Use source model" value={selectedModels.get(activePath) ?? ''} onChange={event => { if (event.currentTarget.value) useModel(Number(event.currentTarget.value)); }}><option value="">First model (native) · choose to materialize another</option>{modelFamily.models.map(item => <option value={item.number} key={item.number}>Model {item.number} · {item.chains.length} chains</option>)}</select><span className="mt-1 block">Choosing a model saves that slice as the new source and resets source-coordinate selections.</span></label>}
                    {target && typeof active === 'number' && <><label className={small}>{document?.format === 'fasta' ? 'FASTA record / native chain' : 'Target chains'}<input className={input} aria-label={`targets.${active}.chains`} placeholder="All chains (native)" value={textValue(target.chains)} onChange={event => update(active, 'chains', event.currentTarget.value)} /></label>
                        {document?.format === 'fasta' && Object.keys(document.records || {}).length > 1 && <select className={input} aria-label="FASTA record" value={textValue(target.chains)} onChange={event => update(active, 'chains', event.currentTarget.value)}><option value="">Choose record</option>{Object.keys(document.records || {}).map(id => <option key={id}>{id}</option>)}</select>}
                        <div className="flex flex-wrap gap-2" aria-label="Target chain selection">{chains.map(chain => <button type="button" key={chain.id} aria-pressed={included.includes(chain.id)} className={`${button} ${included.includes(chain.id) ? 'border-accent bg-accent/10 text-accent' : ''}`} onClick={() => update(active, 'chains', (included.includes(chain.id) ? included.filter(id => id !== chain.id) : [...included, chain.id]).join(','))}>{chain.id} <span className={small}>{chain.length} aa</span></button>)}</div>
                        <div className="flex gap-2" role="tablist" aria-label="Residue selection mode">{(['hotspots', 'coldspots'] as const).map(kind => <button type="button" key={kind} role="tab" aria-selected={mode === kind} className={`${button} ${mode === kind ? 'bg-accent/10 text-accent border-accent' : ''}`} onClick={() => setMode(kind)}>{kind === 'hotspots' ? 'Hotspots' : 'Coldspots'}</button>)}</div>
                        {(['hotspots', 'coldspots'] as const).map(kind => <label className={small} key={kind}>{kind === 'hotspots' ? 'Hotspot residues' : 'Coldspot residues'}<input className={input} aria-label={`targets.${active}.${kind}`} value={textValue(target[kind])} placeholder="A35,A40-48" onChange={event => update(active, kind, event.currentTarget.value)} /></label>)}
                        <p className={small}>Click the sequence or structure to edit {mode}. Native spans use chain letters and residue numbers; insertion variants at the same number share a selection. Replacing the source clears its old chain and residue selections.</p>
                    </>}
                    {active === 'scaffold' && chains.length > 0 && <><div><h5 className="text-sm font-medium">Scaffold chains</h5><p className={small}>All source chains are part of the native scaffold. To use a subset, choose chains and save it as a new source.</p></div><div className="flex flex-wrap gap-2">{chains.map(chain => <button type="button" className={`${button} ${chosenScaffoldChains.includes(chain.id) ? 'bg-accent/10 text-accent' : ''}`} aria-pressed={chosenScaffoldChains.includes(chain.id)} key={chain.id} onClick={() => setScaffoldChains({ path: activePath, ids: chosenScaffoldChains.includes(chain.id) ? chosenScaffoldChains.filter(id => id !== chain.id) : [...chosenScaffoldChains, chain.id] })}>{chain.id} · {chain.length} aa</button>)}</div><button type="button" className={button} disabled={!chosenScaffoldChains.length || busy !== null} onClick={() => void useScaffoldSubset()}>Use selected scaffold chains</button></>}
                    {loaded.error && <div role="alert" className="space-y-2 text-sm text-red-400"><p>{loaded.error}</p><button type="button" className={button} onClick={loaded.retry}>Retry preview</button></div>}
                    {!document && activePath && !loaded.error && <p role="status" className={small}>Loading selected source…</p>}
                    {!activePath && <p className={small}>Choose a source above to inspect its structure and sequence.</p>}
                </div>
                <div className="min-w-0 space-y-4">
                    {document && document.format !== 'fasta' && view === 'structure' && <EpitopeMolstarViewer key={`${activePath}:${model?.number}`} pdbData={model?.content || document.content} format={document.format} height={380} selectedResidueRefs={active === 'scaffold' ? [] : visibleChains.flatMap(chain => chain.residues.filter(residue => selected.has(residueKey(residue))).map(residue => ({ documentId: 'primary', authAsymId: residue.chainId, authSeqId: residue.resNum, insertionCode: residue.iCode })))} onResidueRefClick={residue => { if (active === 'scaffold') return; const candidate = visibleChains.flatMap(chain => chain.residues).find(item => item.chainId === residue.authAsymId && item.resNum === residue.authSeqId && (item.iCode || '') === (residue.insertionCode || '')); if (!candidate) return; const after = new Set(selected); const key = residueKey(candidate); if (after.has(key)) after.delete(key); else after.add(key); applyResidues(after); }} />}
                    {visibleChains.length > 0 && (active === 'scaffold' ? <ScaffoldSequences chains={chains} /> : <div key={`${activePath}:${mode}`}><EpitopeSelector chains={visibleChains} selectedResidues={selected} onSelectionChange={applyResidues} selectedLabel={mode === 'hotspots' ? 'Hotspots' : 'Coldspots'} /></div>)}
                    {document?.format === 'fasta' && !visibleChains.length && <p className={small}>Choose a FASTA record to inspect its sequence.</p>}
                </div>
            </div>
        </div>}
    </section>;
}
function ScaffoldSequences({ chains }: { chains: Chain[] }) {
    return <div className="space-y-3" aria-label="Scaffold sequences">{chains.map(chain => <details open key={chain.id} className="rounded-lg border border-[var(--border-color)] p-3"><summary className="cursor-pointer text-sm font-medium">Chain {chain.id} · {chain.length} residues</summary><p className="mt-2 break-all font-mono text-xs leading-6 text-[var(--text-secondary)]">{chain.sequence}</p></details>)}</div>;
}
