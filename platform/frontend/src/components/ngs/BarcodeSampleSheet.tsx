import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { uploadFile } from '../../lib/api';

const CONTROL = 'w-full rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-2 text-sm text-[var(--text-primary)]';
export function BarcodeSampleSheet({ path, kit, onChange, onBlocker }: {
    path: string; kit: string; onChange: (path: string) => void; onBlocker: (message: string) => void;
}) {
    const [mode,setMode]=useState<'file'|'table'>('file');
    const [experiment,setExperiment]=useState(''); const [flowCell,setFlowCell]=useState(''); const [position,setPosition]=useState('');
    const [rows,setRows]=useState([{barcode:'barcode01',alias:''}]);
    const [dirty,setDirty]=useState(false); const [bulk,setBulk]=useState(''); const [error,setError]=useState('');
    const active=useRef(true);
    useEffect(() => { active.current=true; return () => {active.current=false; onBlocker('');}; },[onBlocker]);
    const save=useMutation({
        mutationFn:async(file:File) => {
            if(file.size>1024*1024) throw new Error('Sample sheet must be at most 1 MiB.');
            const result=await uploadFile(`inputs/ont-sample-sheets/${crypto.randomUUID()}`,file);
            if(typeof result.data?.path!=='string' || !result.data.path) throw new Error('No saved sample-sheet path was returned.');
            return result.data.path;
        },
        onMutate:()=>{ onChange(''); setError(''); },
        onSuccess:value=>{if(active.current){onChange(value);setDirty(false);}},
        onError:error=>setError(error instanceof Error ? error.message : 'Sample sheet could not be saved.'),
    });
    useEffect(()=>{onBlocker(save.isPending ? 'Wait for the barcode sample sheet to finish uploading.' : dirty || save.isError ? 'Save or discard the edited barcode sample sheet before submitting.' : '');},[save.isPending,save.isError,dirty,onBlocker]);
    const edit=()=>{setDirty(true);onChange('');setError('');};
    function saveTable(){
        try{
            if(!experiment.trim() || (!flowCell.trim() && !position.trim())) throw new Error('Enter the experiment name and flow cell ID or position from the source POD5 run.');
            if([experiment,flowCell,position].some(value=>/[,"\r\n]/.test(value))) throw new Error('Run identifiers cannot contain commas, quotes or newlines.');
            const aliases=new Set<string>(),barcodes=new Set<string>();
            for(const row of rows){
                if(!/^barcode(?:0[1-9]|[1-8][0-9]|9[0-6])$/.test(row.barcode) || barcodes.has(row.barcode)) throw new Error('Choose a different barcode number for each sample.');
                if(!/^[A-Za-z0-9_-]{1,40}$/.test(row.alias) || /^barcode[0-9]+$/.test(row.alias) || row.alias==='unclassified' || aliases.has(row.alias)) throw new Error('Sample names must be unique: 1–40 letters, numbers, underscores or hyphens; avoid barcode numbers and unclassified.');
                aliases.add(row.alias);barcodes.add(row.barcode);
            }
            const csv='experiment_id,kit,flow_cell_id,position_id,barcode,alias\n'+rows.map(row=>[experiment.trim(),kit,flowCell.trim(),position.trim(),row.barcode,row.alias].join(',')).join('\n')+'\n';
            save.mutate(new File([csv],'samples.csv',{type:'text/csv'}));
        }catch(error){setError(error instanceof Error?error.message:'Check sample names.');}
    }
    return <section className="space-y-3 rounded border border-[var(--border-primary)] p-3" aria-label="Barcode sample names">
        <h4 className="font-semibold text-sm">Barcode sample names (optional)</h4>
        <p className="text-xs text-[var(--text-secondary)]">The kit supplies the barcode sequences. Enter the barcode numbers used for your plasmids and their sample names here. After basecalling, open Runs → Demultiplexed barcode units to map each barcode to its saved plasmid version and submit the analysis batch.</p>
        <p className="text-xs text-[var(--text-secondary)]">Without a sample sheet, reads keep barcode01–barcode96 labels. Custom barcode nucleotide sequences are not supported by this installed kit profile.</p>
        <div className="flex flex-wrap gap-2">{(['file','table'] as const).map(value=><button type="button" key={value} disabled={save.isPending} aria-pressed={mode===value} className="rounded border border-[var(--border-primary)] px-3 py-2 text-xs" onClick={()=>setMode(value)}>{value==='file'?'Upload sample sheet':'Enter barcode samples'}</button>)}</div>
        {mode==='file'?<label className="block text-xs">Dorado sample-sheet CSV<input type="file" aria-label="Upload barcode sample sheet" accept=".csv" disabled={save.isPending} onChange={event=>{const file=event.target.files?.[0];if(file){setDirty(true);save.mutate(file);}event.target.value='';}} className={`${CONTROL} mt-1`} /></label>:<>
            <p className="text-xs text-[var(--text-secondary)]">Run identifiers must match the recorded POD5 metadata; they are checked again before processing.</p>
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">{([['POD5 experiment name',experiment,setExperiment],['Flow cell ID',flowCell,setFlowCell],['Instrument position (optional with flow cell ID)',position,setPosition]] as const).map(([label,value,setter])=><label key={label} className="text-xs">{label}<input aria-label={label} value={value} disabled={save.isPending} onChange={event=>{setter(event.target.value);edit();}} className={`${CONTROL} mt-1`} /></label>)}</div>
            <label className="block text-xs">Paste barcode and sample-name rows<textarea aria-label="Paste barcode sample rows" rows={3} value={bulk} disabled={save.isPending} placeholder={'barcode01\tplasmid_A\nbarcode02\tplasmid_B'} onChange={event=>setBulk(event.target.value)} className={`${CONTROL} mt-1 font-mono`} /></label>
            <button type="button" disabled={!bulk.trim() || save.isPending} className="rounded border border-[var(--border-primary)] px-3 py-2 text-xs" onClick={()=>{try{const parsed=bulk.trim().split(/\r?\n/).map(line=>{const fields=line.split(/[\t,]/).map(value=>value.trim());if(fields.length!==2)throw new Error('Use two columns: barcode number and sample name.');return {barcode:fields[0],alias:fields[1]};});if(parsed.length>96)throw new Error('Use at most 96 barcodes.');setRows(parsed);setBulk('');edit();}catch(error){setError(String(error));}}}>Apply pasted rows</button>
            <div className="max-h-64 space-y-2 overflow-y-auto">{rows.map((row,index)=><div key={index} className="grid grid-cols-[8rem_minmax(0,1fr)_auto] gap-2"><select aria-label={`Barcode ${index+1}`} value={row.barcode} disabled={save.isPending} onChange={event=>{setRows(previous=>previous.map((item,i)=>i===index?{...item,barcode:event.target.value}:item));edit();}} className={CONTROL}>{Array.from({length:96},(_,i)=>`barcode${String(i+1).padStart(2,'0')}`).map(value=><option key={value}>{value}</option>)}</select><input aria-label={`Sample name ${index+1}`} value={row.alias} disabled={save.isPending} onChange={event=>{setRows(previous=>previous.map((item,i)=>i===index?{...item,alias:event.target.value}:item));edit();}} className={CONTROL} /><button type="button" disabled={rows.length===1 || save.isPending} onClick={()=>{setRows(previous=>previous.filter((_,i)=>i!==index));edit();}}>Remove</button></div>)}</div>
            <div className="flex flex-wrap gap-2"><button type="button" disabled={rows.length===96 || save.isPending} onClick={()=>{const next=Array.from({length:96},(_,i)=>`barcode${String(i+1).padStart(2,'0')}`).find(barcode=>!rows.some(row=>row.barcode===barcode))!;setRows(previous=>[...previous,{barcode:next,alias:''}]);edit();}} className="rounded border border-[var(--border-primary)] px-3 py-2 text-xs">Add barcode sample</button><button type="button" disabled={save.isPending} onClick={saveTable} className="rounded border border-[var(--border-primary)] px-3 py-2 text-xs">Save sample sheet</button></div>
        </>}
        {path && <p role="status" className="break-all text-xs">Selected sample sheet: {path.split('/').pop()}</p>}
        {save.isPending && <p role="status" className="text-xs">Saving sample sheet…</p>}
        {error && <p role="alert" className="text-xs text-[var(--text-error)]">{error}</p>}
        <details><summary className="cursor-pointer text-xs">Existing server sample-sheet path</summary><input aria-label="Sample sheet" value={path} disabled={save.isPending} onChange={event=>{onChange(event.target.value);setDirty(false);save.reset();setError('');}} className={`${CONTROL} mt-2`} /></details>
        {(path || dirty || save.isError) && <button type="button" disabled={save.isPending} onClick={()=>{onChange('');setDirty(false);save.reset();setError('');}} className="text-xs underline">Continue with barcode numbers only</button>}
    </section>;
}
