import { useId } from 'react';
/** Exact discovery-owned task dictionaries. Choosing a key never copies its
 * inherited fields into sparse scientific overrides or clears a custom source. */
export function LaProteinaTaskCatalog({ task, catalog, customSource, onChange }: {
    task: string; catalog: Record<string, Record<string, unknown>>; customSource: boolean; onChange: (key: string) => void;
}) {
    const id = useId();
    const selected = catalog[task];
    return <fieldset><legend>Native motif task catalog</legend>
        <label>Task key<input aria-label="La native motif task" list={id} value={task} onChange={e => onChange(e.target.value)} /></label>
        <datalist id={id}>{Object.keys(catalog).map(key => <option key={key} value={key} />)}</datalist>
        <p>{customSource ? 'Custom motif PDB takes precedence over the selected task dictionary. The named task remains retained.' : 'The selected dictionary is inherited; only explicitly authored overrides replace its fields.'} Installed benchmark files are not verified by discovery.</p>
        {selected ? <dl>{Object.entries(selected).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value === null ? 'Native null' : typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl> : task && <p>Manual native task key retained; no discovery preview is available.</p>}
    </fieldset>;
}
