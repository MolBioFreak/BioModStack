import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    commitMolBioSequenceImport, previewMolBioSequenceImport, fetchNucleotideSequences, fetchMolBioSequenceRevisions,
    type MolBioSequenceRevision, type MolBioSequenceImportPayload, type MolBioSequenceImportCommitResponse, type MolBioSequenceImportPreviewResponse,
} from '../../lib/api';
import { addMolecularProjectMember, MOLECULAR_MEMBER_ADAPTER } from '../../lib/molecularProjectMembership';
import { attachExistingEntity, getProject } from '../../lib/projectManager';
import { useGlobalExperimentContext } from '../experiments/GlobalExperimentContext';

type RecordRef = MolBioSequenceImportCommitResponse['records'][number];
type Preview = { payload: MolBioSequenceImportPayload; result: MolBioSequenceImportPreviewResponse; label: string };
const CONTROL = 'w-full rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2 text-sm text-[var(--text-primary)]';
const BUTTON = 'rounded border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] disabled:opacity-40';
const MAX_BYTES = 10 * 1024 * 1024;
function errorText(error: unknown) {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof detail === 'string' ? detail : error instanceof Error ? error.message : 'Unable to add sequences.';
}

export function ReferenceSequenceInputs({ onAdded }: { onAdded: (records: RecordRef[], stateRevisionId?: string) => void }) {
    const context = useGlobalExperimentContext();
    const active = useRef(true);
    const owner = [context.workspaceId, context.globalExperimentId, context.selectedDomainExperiment?.domain_experiment_id, context.stateRevisionId].join(':');
    const currentOwner = useRef(owner); currentOwner.current = owner;
    useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
    const client = useQueryClient();
    const [tab, setTab] = useState<'paste' | 'files' | 'saved'>('paste');
    const [format, setFormat] = useState<'fasta' | 'genbank' | 'raw_dna'>('fasta');
    const [text, setText] = useState('');
    const [name, setName] = useState('');
    const [topology, setTopology] = useState<'circular' | 'linear'>('circular');
    const [files, setFiles] = useState<File[]>([]);
    const [previews, setPreviews] = useState<Preview[]>([]);
    const [savedRecords, setSavedRecords] = useState<RecordRef[]>([]);
    const [completed, setCompleted] = useState<Set<string>>(new Set());
    const [message, setMessage] = useState('');
    const [search, setSearch] = useState('');
    const [offset, setOffset] = useState(0);
    const [selected, setSelected] = useState<Record<string, string>>({});
    const [addToProject, setAddToProject] = useState(true);
    const domainId = context.selectedDomainExperiment?.domain_experiment_id;
    const canAttach = Boolean(context.workspaceId && context.globalExperimentId && domainId && context.availability.canMutateDomain);
    const library = useQuery({
        queryKey: ['molbio-ngs-sequences', 'reference-search', search, offset],
        queryFn: async () => (await fetchNucleotideSequences({ search, offset, limit: 50, sequence_type: 'dna', sort_by: 'name', sort_desc: false })).data,
        enabled: tab === 'saved', retry: false,
    });
    const revisions = useQuery<Record<string, MolBioSequenceRevision[]>>({
        queryKey: ['molbio-ngs-revisions', 'reference-selection', Object.keys(selected).join(':')],
        queryFn: async () => Object.fromEntries(await Promise.all(Object.keys(selected).map(async id => [id, (await fetchMolBioSequenceRevisions(id)).data]))),
        enabled: tab === 'saved' && Object.keys(selected).length > 0, retry: false,
    });
    const resetPreview = () => { setPreviews([]); setSavedRecords([]); setCompleted(new Set()); setMessage(''); };
    const preview = useMutation({
        mutationFn: async () => {
            const payloads: Array<{ payload: MolBioSequenceImportPayload; label: string }> = [];
            const common = { origin_surface: 'ngs' as const, topology_default: topology };
            if (tab === 'files') {
                if (!files.length) throw new Error('Choose FASTA or GenBank files. You can select several files at once.');
                if (files.length > 256) throw new Error('Choose at most 256 sequence files per batch.');
                if (files.reduce((sum, file) => sum + file.size, 0) > MAX_BYTES) throw new Error('Choose at most 10 MiB of sequence files per batch.');
                for (const file of files) {
                    const sourceFormat = /\.(gb|gbk|gbff|genbank)$/i.test(file.name) ? 'genbank' : /\.(fa|fas|fasta|fna|fsa)$/i.test(file.name) ? 'fasta' : null;
                    if (!sourceFormat) throw new Error(`Unsupported sequence file: ${file.name}. Choose FASTA or GenBank.`);
                    payloads.push({ label: file.name, payload: { ...common, source_format: sourceFormat, source_text: await file.text(), source_provider: 'upload', source_id: file.name.slice(0,255), idempotency_key: crypto.randomUUID() } });
                }
            } else {
                if (!text.trim()) throw new Error('Paste a sequence before checking it.');
                if (new Blob([text]).size > MAX_BYTES) throw new Error('Paste at most 10 MiB per batch.');
                if (format === 'raw_dna' && !name.trim()) throw new Error('Give the DNA sequence a name.');
                payloads.push({ label: 'Pasted sequences', payload: { ...common, source_format: format, source_provider: 'paste', idempotency_key: crypto.randomUUID(),
                    ...(format === 'raw_dna' ? { raw_rows: [{ name: name.trim(), sequence: text.replace(/\s/g, '').toUpperCase(), topology }] } : { source_text: text }) } });
            }
            const result: Preview[] = [];
            for (const item of payloads) {
                result.push({ ...item, result: (await previewMolBioSequenceImport(item.payload)).data });
                if (result.reduce((sum, item) => sum + item.result.records.length, 0) > 256) throw new Error('Add at most 256 sequences per batch.');
            }
            return result;
        },
        onMutate: resetPreview,
        onSuccess: result => { setPreviews(result); setMessage('Review the sequences below, then save them.'); },
        onError: error => setMessage(errorText(error)),
    });
    const add = useMutation({
        mutationFn: async () => {
            const requestOwner = currentOwner.current;
            const assertCurrent = () => { if (!active.current || currentOwner.current !== requestOwner) throw new Error('The sequence workspace changed. Saved sequences remain in MolBio; reopen the Project to finish attaching them.'); };
            let records = savedRecords;
            const finished = new Set(completed);
            if (tab === 'saved') {
                if (!Object.keys(selected).length) throw new Error('Select one or more saved sequences.');
                records = await Promise.all(Object.entries(selected).map(async ([sequenceId, exactRevision]) => {
                    const versions = (await fetchMolBioSequenceRevisions(sequenceId)).data;
                    const version = exactRevision ? versions.find(item => item.id === exactRevision) : versions.find(item => item.is_current);
                    if (!version) throw new Error('A selected version is unavailable. Refresh and select it again.');
                    return { sequence_id: sequenceId, revision_id: version.id, name: library.data?.find(item => item.id === sequenceId)?.name ?? sequenceId };
                }));
            } else {
                if (!previews.length || previews.some(item => !item.result.valid || item.result.errors.length)) throw new Error('Check and correct every sequence before saving.');
                for (const item of previews) {
                    assertCurrent();
                    if (finished.has(item.payload.idempotency_key)) continue;
                    const response = await commitMolBioSequenceImport(item.payload);
                    records = [...records, ...response.data.records];
                    finished.add(item.payload.idempotency_key);
                    setSavedRecords(records); setCompleted(new Set(finished));
                }
            }
            let stateId = context.stateRevisionId ?? undefined;
            if (tab === 'saved' || (addToProject && canAttach)) {
                if (!canAttach) throw new Error('Choose a Project before adding these references.');
                for (const record of records) {
                    assertCurrent();
                    if (!record.revision_id) throw new Error(`No saved version was returned for ${record.name}.`);
                    const identity = new URLSearchParams({ sequence_id: record.sequence_id, revision_id: record.revision_id }).toString();
                    const state = await addMolecularProjectMember(context.workspaceId!, context.globalExperimentId!, domainId!, identity, stateId);
                    stateId = state.id;
                    assertCurrent();
                    const project = await getProject(context.workspaceId!);
                    await attachExistingEntity(context.workspaceId!, context.globalExperimentId!, domainId!, {
                        adapter_id: MOLECULAR_MEMBER_ADAPTER, entity_id: `${identity}&domain_experiment_id=${encodeURIComponent(domainId!)}`,
                        operation: 'attach_reference', role: 'references', note: 'Added as an NGS expected sequence', expected_head_generation: project.head_generation,
                    });
                }
            }
            assertCurrent();
            return { requestOwner, records, stateId: tab === 'saved' || (addToProject && canAttach) ? stateId : undefined };
        },
        onSuccess: async ({ requestOwner, records, stateId }) => {
            if (!active.current || currentOwner.current !== requestOwner) return;
            setSavedRecords(records); setMessage(`${records.length} sequence${records.length === 1 ? '' : 's'} ${stateId ? 'added to this Project' : 'saved to MolBio'}.`);
            await Promise.all([client.invalidateQueries({ queryKey: ['molbio-ngs-sequences'] }), client.invalidateQueries({ queryKey: ['project-manager'] }), client.invalidateQueries({ queryKey: ['molbio-project-hub'] }), client.invalidateQueries({ queryKey: ['molbio-ngs-domain-state'] })]);
            if (active.current && currentOwner.current === requestOwner) onAdded(records, stateId);
        },
        onError: error => setMessage(`${errorText(error)}${savedRecords.length ? ' Saved sequences are retained; retry to finish adding them.' : ''}`),
    });
    const recheck = useMutation({
        mutationFn: async ({index, ordinal, topology}: {index:number; ordinal:number; topology:'circular'|'linear'}) => {
            const item=previews[index];
            const payload={...item.payload,idempotency_key:crypto.randomUUID(),topology_overrides:{...item.payload.topology_overrides,[ordinal]:topology}};
            return {index,preview:{...item,payload,result:(await previewMolBioSequenceImport(payload)).data}};
        },
        onMutate:({index})=>setPreviews(previous=>previous.map((item,i)=>i===index?{...item,result:{...item.result,valid:false}}:item)),
        onSuccess:({index,preview})=>setPreviews(previous=>previous.map((item,i)=>i===index?preview:item)),
        onError:error=>setMessage(errorText(error)),
    });
    const busy = preview.isPending || add.isPending || recheck.isPending;
    const valid = previews.length > 0 && previews.every(item => item.result.valid && !item.result.errors.length);
    return <section className="space-y-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3" data-testid="molbio-sequence-import-panel">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Add expected sequences</h3>
        <p className="text-xs text-[var(--text-secondary)]">Add one plasmid or a batch. Each FASTA or GenBank record stays a separate saved sequence with its own version.</p>
        <p className="text-xs text-[var(--text-secondary)]">For barcoded plasmids, add all expected sequences here. Use Basecall DNA with your barcode kit and sample names, then map the demultiplexed barcodes to these plasmids in Runs.</p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Sequence input method">{([['paste','Paste sequence'],['files','Browse sequence files'],['saved','Saved MolBio sequences']] as const).map(([id,label]) => <button type="button" key={id} disabled={busy} aria-pressed={tab===id} onClick={() => { setTab(id); resetPreview(); }} className={`${BUTTON} ${tab===id ? 'border-[var(--accent-primary)] bg-[var(--bg-tertiary)]' : ''}`}>{label}</button>)}</div>
        {tab === 'saved' ? <>
            <input type="search" aria-label="Search saved MolBio sequences" placeholder="Search saved plasmids by name" value={search} disabled={busy} onChange={event => { setSearch(event.target.value); setOffset(0); }} className={CONTROL} />
            <p className="text-xs text-[var(--text-secondary)]">Select several sequences to add them together. Current versions are pinned when you add them; choose a historical version below if needed.</p>
            {library.isError && <p role="alert">Saved sequences could not be loaded. <button type="button" onClick={() => void library.refetch()}>Retry</button></p>}
            <div className="max-h-72 space-y-2 overflow-y-auto">{library.data?.map(sequence => <div key={sequence.id} className="rounded border border-[var(--border-primary)] p-2">
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label={`Select ${sequence.name}`} disabled={busy} checked={sequence.id in selected} onChange={event => setSelected(previous => { const next={...previous}; if(event.target.checked) next[sequence.id]=''; else delete next[sequence.id]; return next; })} />{sequence.name}</label>
                {sequence.id in selected && <select aria-label={`Version for ${sequence.name}`} value={selected[sequence.id]} disabled={busy} onChange={event => setSelected(previous => ({...previous,[sequence.id]:event.target.value}))} className={`${CONTROL} mt-2`}><option value="">Current version when added</option>{revisions.data?.[sequence.id]?.map(version => <option value={version.id} key={version.id}>Version {version.revision_number}{version.is_current ? ' · current' : ' · historical'}</option>)}</select>}
            </div>)}</div>
            {!library.isLoading && library.data?.length===0 && <p className="text-sm">No saved sequences match.</p>}
            <div className="flex gap-2"><button type="button" className={BUTTON} disabled={!offset || busy} onClick={() => setOffset(value => Math.max(0,value-50))}>Previous sequences</button><button type="button" className={BUTTON} disabled={library.data?.length!==50 || busy} onClick={() => setOffset(value => value+50)}>More sequences</button></div>
            <button type="button" className={BUTTON} disabled={busy || !Object.keys(selected).length || !canAttach} onClick={() => add.mutate()}>Add {Object.keys(selected).length || 'selected'} saved sequences to Project</button>
        </> : <>
            <label className="block text-xs">Default topology<select aria-label="Import topology default" value={topology} disabled={busy} onChange={event => { setTopology(event.target.value as typeof topology); resetPreview(); }} className={`${CONTROL} mt-1`}><option value="circular">Circular plasmids</option><option value="linear">Linear sequences</option></select></label>
            {tab === 'files' ? <label className="block text-sm">FASTA or GenBank files<input type="file" aria-label="Sequence files" multiple accept=".fa,.fas,.fasta,.fna,.fsa,.gb,.gbk,.gbff,.genbank" disabled={busy} onChange={event => { setFiles(Array.from(event.target.files ?? [])); resetPreview(); }} className={`${CONTROL} mt-1`} /><span className="mt-1 block text-xs text-[var(--text-secondary)]">Browse this computer; select several files or one file containing multiple records. Maximum 256 sequences / 10 MiB per batch.</span>{files.map((file,index) => <span className="block text-xs" key={`${file.name}:${index}`}>{file.name}</span>)}</label> : <>
                <label className="block text-xs">Sequence format<select aria-label="Pasted sequence format" value={format} disabled={busy} onChange={event => { setFormat(event.target.value as typeof format); resetPreview(); }} className={`${CONTROL} mt-1`}><option value="fasta">FASTA — one or multiple records</option><option value="genbank">GenBank — one or multiple records</option><option value="raw_dna">Plain DNA sequence</option></select></label>
                {format==='raw_dna' && <input aria-label="Sequence name" placeholder="Plasmid name" value={name} disabled={busy} onChange={event => { setName(event.target.value); resetPreview(); }} className={CONTROL} />}
                <textarea aria-label="Paste expected sequences" rows={7} value={text} disabled={busy} onChange={event => { setText(event.target.value); resetPreview(); }} placeholder={format==='fasta' ? '>plasmid_1\nPaste sequence here\n>plasmid_2\nPaste another sequence here' : format==='genbank' ? 'Paste complete GenBank records here' : 'Paste DNA bases here'} className={`${CONTROL} font-mono`} />
            </>}
            {canAttach && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={addToProject} disabled={busy} onChange={event => setAddToProject(event.target.checked)} />Add saved sequences to this Project</label>}
            <div className="flex flex-wrap gap-2"><button type="button" disabled={busy} className={BUTTON} onClick={() => preview.mutate()}>{preview.isPending ? 'Checking sequences…' : 'Check sequences'}</button><button type="button" disabled={busy || !valid} className={BUTTON} onClick={() => add.mutate()}>{add.isPending ? 'Adding sequences…' : canAttach && addToProject ? 'Save and add to Project' : 'Save sequences to MolBio'}</button></div>
        </>}
        {!canAttach && <p className="text-xs text-[var(--text-secondary)]"><Link to="/projects" className="underline">Choose a Project</Link> to attach expected sequences here. Imports can still be saved to MolBio.</p>}
        {previews.map((item,index) => <div key={item.payload.idempotency_key} className="space-y-1 text-xs"><p className="font-semibold">{item.label}</p>{item.result.records.map(record => <label className="flex flex-wrap items-center gap-2" key={record.record_ordinal}>{record.name} · {record.length?.toLocaleString()} bases<select aria-label={`Topology for ${record.name}`} disabled={busy || completed.size>0} value={record.topology} onChange={event=>recheck.mutate({index,ordinal:record.record_ordinal,topology:event.target.value as 'circular'|'linear'})} className="rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-1"><option value="circular">Circular</option><option value="linear">Linear</option></select></label>)}{item.result.errors.map((error,index) => <p role="alert" key={index} className="text-[var(--text-error)]">{error.message}</p>)}</div>)}
        {message && <p role={preview.isError || add.isError ? 'alert' : 'status'} className="text-sm text-[var(--text-secondary)]">{message}</p>}
        {savedRecords.length>0 && <ul data-testid="molbio-import-committed-records" className="space-y-1 text-xs">{savedRecords.map((record,index) => <li key={`${record.sequence_id}:${record.revision_id}:${index}`}><Link className="underline" to={context.contextHref('/designer',{molbio_sequence_id:record.sequence_id,molbio_revision_id:record.revision_id ?? null})}>{record.name} — open saved version</Link></li>)}</ul>}
    </section>;
}
