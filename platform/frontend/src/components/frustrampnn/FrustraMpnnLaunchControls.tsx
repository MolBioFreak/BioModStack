import { ExecutionTargetPicker } from '../ExecutionTargetPicker';
import type { FrustraMpnnLaunchOptions } from '../../lib/frustraMpnnApi';

/** Destination is independent of scientific source lineage and worker placement. */
export function FrustraMpnnLaunchControls({ value, onChange, disabled = false }: {
    value: FrustraMpnnLaunchOptions; onChange: (value: FrustraMpnnLaunchOptions) => void; disabled?: boolean;
}) {
    const context = new URLSearchParams(window.location.search).get('launch_context_id');
    return <section aria-label="FrustraMPNN launch destination and placement">
        <label>Destination<select aria-label="FrustraMPNN destination" disabled={disabled} value={value.launch_context_id ?? ''} onChange={e => onChange({ ...value, launch_context_id: e.target.value || null })}>
            <option value="">Standalone</option>
            {context && <option value={context}>Selected Project launch context</option>}
            {value.launch_context_id && value.launch_context_id !== context && <option value={value.launch_context_id}>{value.launch_context_id}</option>}
        </select></label>
        <p>{value.execution_target_id === undefined ? 'Placement unchanged (not supplied).' : value.execution_target_id === null ? 'Explicit Local placement.' : 'Selected worker placement.'}</p>
        <ExecutionTargetPicker value={value.execution_target_id ?? null} onChange={execution_target_id => onChange({ ...value, execution_target_id })} disabled={disabled} />
        <button type="button" disabled={disabled} onClick={() => { const { execution_target_id: _target, ...rest } = value; onChange(rest); }}>Leave placement unchanged</button>
    </section>;
}
