/*
 * Real demo plasmids sourced from public Addgene browse sequence pages.
 * The generated dataset lives in `demoConstructs.generated.ts`.
 */

import type { SequenceData } from './types';

export async function loadDemoPlasmids(): Promise<SequenceData[]> {
    const module = await import('./demoConstructs.generated');
    return module.DEMO_PLASMIDS;
}
