import { expect, it } from 'vitest';
import { metricLabel } from '../../src/lib/cohortAnalytics';

// Keys the RFD4 OF3 refold publishes (job 98f19090 manifest); acronyms must keep their case.
it('labels RFD4 oracle and refold metric keys with correct acronyms', () => {
    const keys = ['overall_plddt', 'overall_ptm', 'overall_pae', 'plddt_coi', 'ptm_coi', 'pae_coi', 'fraction_disordered', 'has_clash', 'rmsd', 'ranking_score'];
    expect(Object.fromEntries(keys.map(k => [k, metricLabel(k)]))).toEqual({
        overall_plddt: 'Overall pLDDT', overall_ptm: 'Overall pTM', overall_pae: 'Overall PAE',
        plddt_coi: 'pLDDT (chain of interest)', ptm_coi: 'pTM (chain of interest)', pae_coi: 'PAE (chain of interest)',
        fraction_disordered: 'Fraction disordered', has_clash: 'Has clash', rmsd: 'RMSD', ranking_score: 'Native ranking score',
    });
});
