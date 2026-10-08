import { expect, it } from 'vitest';
import { workspaceRecoverySnapshot, workspaceHydrate, workspaceSnapshot } from '../../src/lib/repairWorkspaceSnapshot';

const method = { schema: 'bms.bioxp-method.v1', name: 'Recovery input retention', steps: [], editor_state: { extension: { raw: null }, run_inputs: { bindings: { stale: true }, dependencies: { stale: true }, initial_state: { stale: true } } } };
const recovery = { original_job_id: 'original', submitted: false, automatic_setup: [], excluded_actions: [] };
const cases = [
    ['omitted', {}],
    ['null', { bindings: null, dependencies: null, initial_state: null }],
    ['empty', { bindings: {}, dependencies: {}, initial_state: {} }],
    ['retained', { bindings: { amount: '003.7500', future: null }, dependencies: { liquid_classes: [], future: null }, initial_state: { custody: null } }],
] as const;
it.each(cases)('recovery %s replaces stale saved inputs without normalizing the native response', (_name, inputs) => {
    const result = { method, recovery, ...inputs }, before = JSON.stringify(result);
    const recovered = workspaceRecoverySnapshot(result);
    expect(recovered.editor_state).toEqual({ extension: { raw: null }, run_inputs: inputs, recovery_linkage: recovery });
    const saved = workspaceSnapshot(recovered, workspaceHydrate(recovered).inputs);
    expect(saved).toEqual(recovered);
    const cold = workspaceHydrate(JSON.parse(JSON.stringify(saved)));
    expect(workspaceSnapshot(cold.method, cold.inputs)).toEqual(saved);
    expect(JSON.stringify(result)).toBe(before);
    const edited = workspaceSnapshot(cold.method, { ...cold.inputs, initialState: { tips: null } });
    expect(edited.editor_state).toEqual({ ...recovered.editor_state, run_inputs: { ...inputs, initial_state: { tips: null } } });
});
it('copies recovered raw values and retains draft fallback without mutating original snapshots', () => {
    const inputs = { bindings: { amount: '001.00' }, dependencies: { future: null }, initial_state: null };
    const recovered = workspaceRecoverySnapshot({ draft: method, recovery, ...inputs });
    (recovered.editor_state as any).run_inputs.bindings.amount = '002.00';
    expect(inputs.bindings.amount).toBe('001.00');
    expect(method.editor_state.run_inputs.bindings).toEqual({ stale: true });
});
