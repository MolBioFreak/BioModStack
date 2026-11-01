import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = () => readFileSync('src/components/ResultsViewer.tsx', 'utf8');

describe('ResultsViewer review panel at the Resequence loop gate', () => {
    it('shows the working-set panel for a loop job paused at post_structure_validation, not only for antibody jobs', () => {
        const text = source();
        expect(text).toContain("=== 'post_structure_validation'");
        expect(text).toContain("activeJob?.model_id === 'protein_modification_experimental'");
        expect(text).toContain("activeJob?.mode === 'region_redesign'");
        expect(text).toContain('const showReviewWorkingSetPanel = isAntibodyContext || isResequenceReviewContext;');
    });

    it('keeps antibody wording for antibody jobs and offers Continue Workflow plus the optional FrustraMPNN action otherwise', () => {
        const text = source();
        expect(text).toContain("isAntibodyContext ? 'Antibody Refinement' : 'Review Working Set'");
        expect(text).toContain('continueProteinLocalReviewMutation.mutate()');
        expect(text).toContain('Analyze with FrustraMPNN');
        expect(text).toContain("!isAntibodyContext && activeJob?.awaiting_stage === 'post_structure_validation'");
    });
});
