import React from 'react';
import type { ExistingDeNovoGenerator } from './deNovoGeneratorSelection';

export function DeNovoModalityNotice({ generator }: { generator: ExistingDeNovoGenerator | null }) {
    return <React.Fragment>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">
            RFantibody supports antibody designs; this BoltzGen route is VHH-only; PPIFlow needs a seeded antibody-target complex. Other BoltzGen modalities require their model-owned launcher, not a substituted nanobody request.
        </p>
        {!generator && <p role="alert" className="mt-2 text-sm text-amber-300">Saved generator is not available here. Select an engine explicitly before launch.</p>}
    </React.Fragment>;
}
