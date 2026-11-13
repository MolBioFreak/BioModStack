import { primerModelOptions } from '../fixtures/primerModelOptions';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { PrimerPanel } from '../../src/components/MolBioToolkit/panels/PrimerPanel';
import * as api from '../../src/lib/api';

vi.mock('../../src/lib/api', () => ({
 calculatePrimerTm: vi.fn(), calculatePrimerQc: vi.fn(), calculatePrimerThermo: vi.fn(), fetchPrimers: vi.fn(),
 createPrimer: vi.fn(), deletePrimer: vi.fn(), togglePrimerFavorite: vi.fn(), designPrimers: vi.fn(),
}));
vi.mock('../../src/components/MolBioToolkit/PrimerTmSettingsPanel', () => ({ PrimerTmSettingsPanel: () => null }));

let root: Root, host: HTMLDivElement;
const settings: api.PrimerTmSettings = { algorithm: 'wallace', salt_correction: 'none', primer_concentration_nM: 250, template_concentration_nM: 0, na_mM: 50, k_mM: 0, tris_mM: 0, mg_mM: 1.5, dntps_mM: 0.6, dmso_percent: 0, formamide_percent: 0, self_complementary: false };
const noop = () => {};
const fwd = { id: 'f', name: 'FWD', sequence: 'ATACGCTGCTTTAATGCCTTTG', start: 0, end: 22, strand: 1 } as any;
const rev = { id: 'r', name: 'REV', sequence: 'GGAAAGGAGCTGACAGGTG', start: 100, end: 119, strand: -1 } as any;
const seq = (primers: any[]) => ({ name: 'construct', sequence: 'ACGT'.repeat(40), circular: false, sequenceType: 'dna' as const, features: [], primers });
const props = (primers: any[]) => ({ sequenceData: seq(primers), selection: null, onHighlight: noop, onAddPrimer: vi.fn(), onRemovePrimer: noop, tmOptions: primerModelOptions, tmSettings: settings, onTmSettingsChange: noop });
const structure = (dg: number, severity: api.PrimerThermoSeverity, three = false) => ({ dg_kcal_mol: dg, severity, structure: '((..))', three_prime_paired: three });
const thermo = (): { data: api.PrimerThermoResponse } => ({ data: {
 engine: 'ViennaRNA (RNAfold/RNAcofold), built-in DNA Mathews 2004 parameters', engine_version: '2.7.2',
 credit: 'ViennaRNA, (c) Institute for Theoretical Chemistry, University of Vienna; DNA Mathews 2004 energy parameters as built into ViennaRNA.',
 citations: ['Lorenz R. et al. ViennaRNA Package 2.0. Algorithms Mol Biol 2011;6:26.'], caveats: ['Predictions from a nearest-neighbor model, not measurements.'],
 thresholds_kcal_mol: { yellow_at_or_below: -5, orange_at_or_below: -10 }, temperature_c: 37, monovalent_equivalent_mM: 163.8, salt_note: null,
 primers: [
  { id: 'f', name: 'FWD', sequence: fwd.sequence, hairpin: structure(0, 'none'), self_dimer: structure(-11.06, 'orange', true), warnings: [] },
  { id: 'r', name: 'REV', sequence: rev.sequence, hairpin: structure(0, 'none'), self_dimer: structure(-6.05, 'yellow'), warnings: [] },
 ],
 pairs: [{ left: 0, right: 1, left_name: 'FWD', right_name: 'REV', ...structure(-5.92, 'yellow') }],
} });
function deferred<T = any>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
async function render(node: React.ReactNode) { await act(async () => root.render(node)); }
async function click(label: string) { const b = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === label); expect(b, label).toBeDefined(); await act(async () => b!.click()); }
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(250); }); }
const sev = (name: string) => host.querySelectorAll(`[data-testid="primer-thermo"] [data-severity="${name}"]`).length;

beforeEach(() => {
 vi.useFakeTimers(); vi.clearAllMocks(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
 vi.mocked(api.calculatePrimerTm).mockResolvedValue({ data: [] } as any);
 vi.mocked(api.calculatePrimerQc).mockResolvedValue({ data: { primers: [], pairwise: [] } } as any);
 vi.mocked(api.calculatePrimerThermo).mockResolvedValue(thermo() as any);
 vi.mocked(api.fetchPrimers).mockResolvedValue({ data: [] } as any);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });

it('shows hairpin, self-dimer and pair-dimer delta-G with yellow and orange flags, only on the QC tab', async () => {
 await render(<PrimerPanel {...props([fwd, rev])} />); await tick();
 expect(api.calculatePrimerThermo).not.toHaveBeenCalled();
 await click('QC'); await tick();
 expect(api.calculatePrimerThermo).toHaveBeenCalledTimes(1);
 expect(vi.mocked(api.calculatePrimerThermo).mock.calls[0][0]).toEqual({
  primers: [{ id: 'f', name: 'FWD', sequence: fwd.sequence }, { id: 'r', name: 'REV', sequence: rev.sequence }],
  pairs: [{ left: 0, right: 1 }],
  settings: { na_mM: 50, k_mM: 0, tris_mM: 0, mg_mM: 1.5, dntps_mM: 0.6 },
 });
 expect(sev('orange')).toBe(1); expect(sev('yellow')).toBe(2);
 const text = host.querySelector('[data-testid="primer-thermo"]')!.textContent!;
 expect(text).toContain('self-dimer \u221211.06'); expect(text).toContain('3\u2032 end paired');
 expect(text).toContain('hairpin none'); expect(text).toContain('FWD \u00d7 REV'); expect(text).toContain('dimer \u22125.92');
 expect(text).toContain('163.8 mM');
 expect(text).toContain('University of Vienna'); expect(text).toContain('Lorenz R.');
 expect(text).toContain('nothing is blocked');
});

it('is informational: flagged primers stay editable and no control is disabled by a flag', async () => {
 await render(<PrimerPanel {...props([fwd, rev])} />); await click('QC'); await tick();
 expect(sev('orange')).toBe(1);
 expect([...host.querySelectorAll('button')].filter(b => b.disabled)).toHaveLength(0);
});

it('builds pairs only from forward x reverse primers', async () => {
 const fwd2 = { ...fwd, id: 'f2', name: 'FWD2' };
 await render(<PrimerPanel {...props([fwd, fwd2])} />); await click('QC'); await tick();
 expect(vi.mocked(api.calculatePrimerThermo).mock.calls[0][0].pairs).toEqual([]);
 await render(<PrimerPanel {...props([fwd, rev, fwd2])} />); await tick();
 expect(vi.mocked(api.calculatePrimerThermo).mock.calls.at(-1)![0].pairs).toEqual([{ left: 0, right: 1 }, { left: 2, right: 1 }]);
});

it('reports a failed request instead of showing stale numbers', async () => {
 vi.mocked(api.calculatePrimerThermo).mockRejectedValueOnce({ response: { data: { detail: 'at most 100 primers per request' } } });
 await render(<PrimerPanel {...props([fwd, rev])} />); await click('QC'); await tick();
 expect(host.querySelector('[data-testid="primer-thermo"] [role="alert"]')!.textContent).toContain('at most 100 primers per request');
 expect(sev('orange')).toBe(0);
});

it('does not show results for a previous primer set after the primers change', async () => {
 await render(<PrimerPanel {...props([fwd, rev])} />); await click('QC'); await tick();
 expect(sev('orange')).toBe(1);
 const pending = deferred();
 vi.mocked(api.calculatePrimerThermo).mockReturnValueOnce(pending.promise as any);
 await render(<PrimerPanel {...props([fwd])} />); await tick();
 expect(sev('orange')).toBe(0);
 expect(host.querySelector('[data-testid="primer-thermo"]')!.textContent).toContain('Calculating');
 await act(async () => { pending.resolve(thermo()); });
 expect(sev('orange')).toBe(1);
});

it('renders nothing for a construct without primers', async () => {
 await render(<PrimerPanel {...props([])} />); await click('QC'); await tick();
 expect(host.querySelector('[data-testid="primer-thermo"]')).toBeNull(); expect(api.calculatePrimerThermo).not.toHaveBeenCalled();
});
