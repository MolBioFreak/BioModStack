export function BindCraft2ListEditor({ label, value, onChange }: {
    label: string; value: unknown[]; onChange: (value: unknown[]) => void;
}) {
    return <div className="space-y-2" role="group" aria-label={label}>
        {value.map((item, index) => {
            const update = (next: unknown) => onChange(value.map((old, position) => position === index ? next : old));
            return <div key={index} className="rounded border p-2 space-y-2">
                <label>Entry {index + 1} type<select aria-label={`${label}.${index}.type`} value={Array.isArray(item) ? 'list' : typeof item} onChange={event => update(event.currentTarget.value === 'number' ? 0 : event.currentTarget.value === 'list' ? [] : '')}><option value="number">Number</option><option value="string">Text / native identifier</option><option value="list">Nested list</option></select></label>
                {Array.isArray(item) ? <BindCraft2ListEditor label={`${label}.${index}`} value={item} onChange={update} /> :
                    <input aria-label={`${label}.${index}`} type={typeof item === 'number' ? 'number' : 'text'} step="any" value={String(item ?? '')} onChange={event => update(typeof item === 'number' ? Number(event.currentTarget.value) : event.currentTarget.value)} />}
                <button type="button" onClick={() => onChange(value.filter((_, position) => position !== index))}>Remove entry</button>
            </div>;
        })}
        <button type="button" onClick={() => onChange([...value, ''])}>Add {label} entry</button>
    </div>;
}
