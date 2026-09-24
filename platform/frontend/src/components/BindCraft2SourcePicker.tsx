import { useState } from 'react';
import { fetchFiles, uploadFile } from '../lib/api';

/** Existing governed files API; never asks the operator for a server filesystem path. */
export function BindCraft2SourcePicker({ label, value, onChange, structureOnly = false }: {
    label: string; value: string; onChange: (path: string) => void; structureOnly?: boolean;
}) {
    const [entries, setEntries] = useState<Array<{ path: string; name: string; is_directory: boolean }> | null>(null);
    const [folder, setFolder] = useState('/');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    async function browse(path: string) {
        setError(''); setBusy(true);
        try { const response = await fetchFiles(path); setFolder(path); setEntries(response.data.entries); }
        catch (error) { setError(error instanceof Error ? error.message : String(error)); }
        finally { setBusy(false); }
    }
    return <div className="space-y-3 min-w-0 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-3">
        <output aria-label={label} className="block break-all rounded bg-[var(--bg-secondary)] px-3 py-2 text-sm text-[var(--text-secondary)]">{value || 'No source selected'}</output>
        <button type="button" disabled={busy} onClick={() => void browse(folder)}>Browse {label}</button>
        <label className="block">Upload {label}<input type="file" aria-label={`Upload ${label}`} accept={structureOnly ? '.pdb,.cif' : '.pdb,.cif,.fasta,.fa'} disabled={busy} onChange={async event => {
            const file = event.currentTarget.files?.[0]; if (!file) return;
            setError(''); setBusy(true);
            try { const response = await uploadFile('inputs', file); onChange(response.data.path); }
            catch (error) { setError(error instanceof Error ? error.message : String(error)); }
            finally { setBusy(false); }
        }} /></label>
        {entries && <div role="group" aria-label={`Sources for ${label}`} className="max-h-64 overflow-auto rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)] p-2">
            <button type="button" disabled={folder === '/'} onClick={() => void browse('/' + folder.split('/').filter(Boolean).slice(0, -1).join('/'))}>Parent folder</button>
            {entries.filter(entry => entry.is_directory || (structureOnly ? /\.(pdb|cif)$/i : /\.(pdb|cif|fasta|fa)$/i).test(entry.path)).map(entry =>
                <button className="block w-full break-all p-2 text-left" type="button" key={entry.path} onClick={() => entry.is_directory ? void browse(entry.path) : (onChange(entry.path), setEntries(null))}>{entry.is_directory ? 'Folder: ' : ''}{entry.name || entry.path}</button>)}
            <button type="button" onClick={() => setEntries(null)}>Close browser</button>
        </div>}
        {error && <p role="alert">{error}</p>}
    </div>;
}
