import type { BinderRoundDraft } from '../lib/binderRound';
import { hydrateBinderRound } from '../lib/binderRound';
import { initialRoundSteps } from '../lib/binderShell';
import { BinderRoundSettings } from './BinderRoundSettings';

/** Pass this node to NativeBinderGeneration.generationSlot, not beside its shell. */
export function BinderShellGenerationSlot({ generator, values, onChange }: {
    generator: string;
    values: Record<string, UntypedApiValue>;
    onChange: (draft: BinderRoundDraft) => void;
}) {
    const { binder_round } = hydrateBinderRound(values);
    return <section aria-label="Initial generation and candidate round" className="space-y-3">
        <ol aria-label="Initial generation flow">{initialRoundSteps(generator, binder_round).map(step => <li key={step.title}><strong>{step.title}</strong> — {step.detail}</li>)}</ol>
        <BinderRoundSettings values={values} onChange={onChange} />
    </section>;
}
