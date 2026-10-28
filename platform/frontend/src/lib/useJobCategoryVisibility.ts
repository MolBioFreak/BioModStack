import { useEffect, useState } from 'react';
import type { JobCategoryVisibility } from './jobCategories';

/** Each surface keeps its own defaults and preference. A filter is display-only. */
export function useJobCategoryVisibility(
    surface: 'queue' | 'dashboard' | 'results', defaults: JobCategoryVisibility,
): [JobCategoryVisibility, (next: JobCategoryVisibility) => void] {
    const key = `bms.job-categories.${surface}.v1`;
    const [visibility, setVisibility] = useState<JobCategoryVisibility>(() => {
        try {
            const stored: unknown = JSON.parse(localStorage.getItem(key) || 'null');
            if (stored && typeof stored === 'object' && 'ngs' in stored && 'alignments' in stored
                && typeof stored.ngs === 'boolean' && typeof stored.alignments === 'boolean') {
                return { ngs: stored.ngs, alignments: stored.alignments };
            }
        } catch { /* Storage is optional, including in private browsers. */ }
        return { ...defaults };
    });
    useEffect(() => {
        try { localStorage.setItem(key, JSON.stringify(visibility)); } catch { /* optional */ }
    }, [key, visibility]);
    return [visibility, setVisibility];
}
