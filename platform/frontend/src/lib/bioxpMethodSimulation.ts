import type { MethodValue } from './bioxpMethods';

const record = (value: unknown): MethodValue => value && typeof value === 'object' && !Array.isArray(value) ? value as MethodValue : {};

export const methodInitialState = (simulation: unknown, submitted: unknown): unknown => Object.hasOwn(record(simulation), 'initial_state') ? record(simulation).initial_state : submitted;

/** Replay producer deltas from the immutable compile input, never from final state.
 * A missing occurrence is unknown, not permission to display a later snapshot.
 */
export function methodStateAfter(simulation: unknown, initialState: unknown, occurrenceId: unknown): MethodValue | null {
    const snapshots = record(simulation).after_occurrences;
    if (!Array.isArray(snapshots)) return null;
    const index = snapshots.findIndex(row => record(row).occurrence_id === occurrenceId);
    if (index < 0) return null;
    const state = structuredClone(record(methodInitialState(simulation, initialState)));
    for (const snapshot of snapshots.slice(0, index + 1)) {
        const delta = record(record(snapshot).state_delta);
        for (const [key, value] of Object.entries(delta)) {
            if (['vessels', 'labware', 'custody', 'thermal_tasks'].includes(key)) {
                state[key] = { ...record(state[key]), ...structuredClone(record(value)) };
            } else if (key === 'channels') {
                const channels = { ...record(state.channels) };
                for (const [channel, update] of Object.entries(record(value))) {
                    const { contacts_append, contacts_reset, ...fields } = record(update);
                    const prior = record(channels[channel]);
                    channels[channel] = { ...prior, ...structuredClone(fields), contacts: [
                        ...(!contacts_reset && Array.isArray(prior.contacts) ? prior.contacts : []),
                        ...(Array.isArray(contacts_append) ? structuredClone(contacts_append) : []),
                    ] };
                }
                state.channels = channels;
            } else state[key] = structuredClone(value);
        }
    }
    return state;
}

export function methodVessels(state: MethodValue | null) {
    return Object.entries(record(state?.vessels)).map(([key, value]) => {
        const split = key.lastIndexOf(':');
        const labwareId = key.slice(0, split);
        return { key, labwareId, well: key.slice(split + 1), station: record(record(state?.labware)[labwareId]).station, volume_ul: record(value).volume_ul, materials: record(value).materials };
    });
}
