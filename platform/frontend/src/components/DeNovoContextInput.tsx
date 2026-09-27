import { useState } from 'react';
import { FileBrowser } from './FileBrowser';

export interface DeNovoContextInputProps {
    task: 'ligand_conditioned' | 'dna_conditioned' | 'rna_conditioned' | 'custom_json';
    ligandPath: string;
    ligandName: string;
    nativeJsonPath: string;
    sequence: string;
    onChange: (patch: { ligandPath?: string; ligandName?: string; nativeJsonPath?: string; sequence?: string }) => void;
}

const inputClass = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]';
const buttonClass = 'rounded-lg border border-[var(--border-primary)] px-3 py-2 text-sm text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]';

/** Reuse managed acquisition without translating model-native molecular inputs. */
export function DeNovoContextInput(props: DeNovoContextInputProps) {
    return <ContextInput key={props.task} {...props} />;
}

function ContextInput({ task, ligandPath, ligandName, nativeJsonPath, sequence, onChange }: DeNovoContextInputProps) {
    const [browsing, setBrowsing] = useState(false);
    const isSequence = task === 'dna_conditioned' || task === 'rna_conditioned';
    const isLigand = task === 'ligand_conditioned';
    const label = isLigand ? 'Ligand SDF' : 'Native input JSON';
    const selectedPath = isLigand ? ligandPath : nativeJsonPath;
    const selectFile = (path: string) => {
        onChange(isLigand ? { ligandPath: path } : { nativeJsonPath: path });
        setBrowsing(false);
    };
    const clear = () => {
        setBrowsing(false);
        onChange(isSequence ? { sequence: '' } : isLigand ? { ligandPath: '' } : { nativeJsonPath: '' });
    };
    return <div className="space-y-3">
        {isSequence ? <label className="block text-sm text-[var(--text-primary)]">DNA/RNA sequence
            {/* Protein sequence libraries and duplex editors have different scientific
                contracts. Keep this native scalar literal, including saved values. */}
            <textarea className={`${inputClass} mt-1 font-mono`} rows={4} value={sequence}
                onChange={event => onChange({ sequence: event.target.value })} />
        </label> : <>
            <div className="flex flex-wrap items-center gap-3">
                <button type="button" className={buttonClass} onClick={() => setBrowsing(true)}>Upload / browse {label}</button>
                <span aria-label={`Selected ${label}`} className="break-all text-sm text-[var(--text-secondary)]">{selectedPath || 'No file selected'}</span>
            </div>
            {isLigand && <label className="block text-sm text-[var(--text-primary)]">Ligand name
                <input className={`${inputClass} mt-1`} value={ligandName} onChange={event => onChange({ ligandName: event.target.value })} />
            </label>}
            <details>
                <summary className="cursor-pointer text-xs text-[var(--text-secondary)]">Use existing path</summary>
                <label className="mt-2 block text-sm text-[var(--text-primary)]">{label} path
                    <input className={`${inputClass} mt-1`} value={selectedPath} onChange={event => selectFile(event.target.value)} />
                </label>
            </details>
            {browsing && <FileBrowser accept={isLigand ? '.sdf' : '.json'} title={`Select ${label}`}
                onCancel={() => setBrowsing(false)} onSelect={selectFile} />}
        </>}
        <button type="button" className={buttonClass} onClick={clear}>Clear {isSequence ? 'sequence' : label}</button>
    </div>;
}
