import type { BinderRoundRequest } from './binderRound';
import type { BC2Request } from '../components/BindCraft2Settings';
import type { BC2Source } from './bindcraft2StructureInputs';
import { portableNativeSource } from './nativeBinderAuthoring';
import type { BinderSourceHandoff } from '../components/BinderGeneratorChooser';

const names: Record<string, string> = { fampnn: 'FA-MPNN', proteinmpnn: 'ProteinMPNN', caliby_binder: 'Caliby', protenix: 'Protenix', boltz2: 'Boltz2', esmfold2: 'ESMFold2', bindcraft2: 'BindCraft2', rfantibody: 'RFantibody', ppiflow: 'PPIFlow', boltzgen: 'BoltzGen' };
export function initialRoundSteps(generator: string | null, round: BinderRoundRequest) {
    const steps = [{ title: names[generator ?? ''] ?? 'Generator', detail: 'Initial candidate generation' }];
    if (round.enabled) steps.push(
        { title: names[round.sequence_design.model_id] ?? round.sequence_design.model_id, detail: 'Sequence design only for producer-declared backbone-only candidates' },
        { title: names[round.prediction.model_id] ?? round.prediction.model_id, detail: 'Blind prediction of candidate sequences' },
    );
    return steps;
}
export function binderEngineSearch(search: string, engine: string): string {
    const query = new URLSearchParams(search);
    query.set('template', 'antibody_denovo'); query.set('engine', engine);
    query.delete('model'); query.delete('mode');
    return `?${query}`;
}
export interface BC2ShellSourceHandoff extends BinderSourceHandoff {
    /** Complete inspection context, never native params. No first-target fallback. */
    bc2?: { settings: BC2Request; references: Record<string, { path: string; source: BC2Source }> };
}
export function bc2SourceHandoff(settings: BC2Request, references: Record<string, { path: string; source: BC2Source }> = {}): BC2ShellSourceHandoff {
    const targets = Array.isArray(settings.targets) ? settings.targets : [];
    const retained = Object.fromEntries(Object.entries(references).filter(([, entry]) => Boolean(entry)).map(([key, entry]) => [key, { ...entry, source: portableNativeSource(entry.source) }]));
    const result: BC2ShellSourceHandoff = { bc2: { settings: structuredClone(settings), references: retained } };
    const target = targets.length === 1 ? targets[0] as Record<string, unknown> : undefined;
    // Path and provenance only: destination owns chain/hotspot grammar and conversion.
    if (target && typeof target.target_path === 'string' && !/\.(fa|fasta|faa)$/i.test(target.target_path)) {
        const entry = retained['target:0'];
        const source = entry?.path === target.target_path ? entry.source : undefined;
        result.target = { path: target.target_path, name: source?.name, modelNumber: source?.modelNumber,
            ...(source ? { reference: { ...source, path: target.target_path, type: source.designId ? 'run' as const : 'upload' as const } } : {}) };
    }
    if (typeof settings.binder_scaffold === 'string' && settings.binder_scaffold) result.framework = { path: settings.binder_scaffold, name: retained.scaffold?.source.name };
    return result;
}
/** Return-time writes do not overwrite any destination source field, even empty. */
export function adoptShellSourcePath(values: Record<string, unknown>, key: string, path?: string) {
    return path && !Object.hasOwn(values, key) ? { ...values, [key]: path } : values;
}
export function createShellPreviewAttempt() {
    let attempt = 0;
    return { begin: () => ++attempt, isCurrent: (token: number) => token === attempt };
}
