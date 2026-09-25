import React from 'react';
import type { ExistingDeNovoGenerator } from './deNovoGeneratorSelection';

export function DeNovoModalityNotice({ generator }: { generator: ExistingDeNovoGenerator | null }) {
    return <React.Fragment>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Start binder generation with BindCraft2, BoltzGen, RFantibody, or PPIFlow. Choose the format and objective to open the engine’s native mode. Only the legacy BoltzGen wrapper here is VHH-only; only the retained PPIFlow partial-flow wrapper requires a seeded antibody-target complex. These legacy limits do not apply to the native generation modes.
        </p>
        {!generator && <p role="alert" className="mt-2 text-sm text-amber-300">Saved generator is not available here. Select an engine explicitly before launch.</p>}
    </React.Fragment>;
}
