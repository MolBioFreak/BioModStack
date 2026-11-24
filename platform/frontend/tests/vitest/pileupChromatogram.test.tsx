import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { PileupChromatogram, parsePileupTable } from '../../src/components/ngs/PileupChromatogram';

const HEADER = 'chrom\tposition_1based\treference_base\tdepth\tforward_depth\treverse_depth\ta_count\tc_count\tg_count\tt_count\tn_count\tinsertion_count\tdeletion_count\tconsensus_base\tmajor_allele_fraction\tlow_coverage\tambiguous';
function table(length: number): string {
    const rows = [HEADER];
    for (let position = 1; position <= length; position += 1) {
        // Position 60 is mixed (60% T, 40% C); position 70 is deleted in most reads.
        const counts = position === 60 ? [0, 40, 0, 60, 0, 'T'] : position === 70 ? [0, 30, 0, 0, 70, '-'] : [100, 0, 0, 0, 0, 'A'];
        rows.push(`ref\t${position}\tA\t100\t50\t50\t${counts[0]}\t${counts[1]}\t${counts[2]}\t${counts[3]}\t0\t0\t${counts[4]}\t${counts[5]}\t1.0\tfalse\tfalse`);
    }
    return rows.join('\n') + '\n';
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); document.body.replaceChildren(); vi.restoreAllMocks(); });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }

it('reads shares of aligned reads from the per-base table', () => {
    const rows = parsePileupTable(table(80));
    expect(rows).toHaveLength(80);
    expect(rows[59]).toMatchObject({ position: 60, called: 'T', shares: { T: 0.6, C: 0.4, A: 0, G: 0 } });
    expect(parsePileupTable('not a table')).toEqual([]);
});

it('draws the strip from the table, labels it a read pileup, and offers the AB1 file', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: table(200) });
    await act(async () => root.render(<PileupChromatogram tableUrl="/api/jobs/j/ngs-artifacts/table" ab1Url="/api/jobs/j/ngs-artifacts/ab1" initialPosition={60} />));
    await settle();
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0][0]).toBe('/api/jobs/j/ngs-artifacts/table');
    expect(container.textContent).toContain('A read pileup, not a Sanger trace.');
    expect(container.textContent).toContain('At 60, 60% of reads carry T.');
    expect(container.querySelectorAll('[data-pileup-channel]')).toHaveLength(4);
    expect(container.querySelector('[data-pileup-base="60"]')!.textContent).toBe('T');
    expect(container.querySelector('[data-pileup-base="70"]')!.textContent).toBe('–');
    expect(container.querySelector<HTMLAnchorElement>('[data-testid="pileup-ab1-download"]')!.getAttribute('href')).toBe('/api/jobs/j/ngs-artifacts/ab1');
    // Paging moves one window and stays inside the table.
    const next = container.querySelector<HTMLButtonElement>('button[aria-label="Next positions"]')!;
    await act(async () => next.click());
    expect(container.querySelector<HTMLInputElement>('input[aria-label="Go to position"]')!.value).toBe('104');
    expect(container.querySelector('[data-pileup-base="60"]')).toBeNull();
    for (let i = 0; i < 6; i += 1) await act(async () => next.click());
    expect(container.querySelector('[data-pileup-base="200"]')).not.toBeNull();
    expect(next.disabled).toBe(true);
});

it('says so when there is no AB1 file or the table cannot be read, without hiding anything else', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('offline'));
    await act(async () => root.render(<PileupChromatogram tableUrl="/api/jobs/j/ngs-artifacts/table" />));
    await settle();
    expect(container.textContent).toContain('AB1 file not written for this result');
    expect(container.textContent).toContain('The per-base table could not be read');
    expect(container.querySelector('svg')).toBeNull();
});
