import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { BindCraft2Campaign } from '../../src/components/BindCraft2Campaign';
import type { BC2CampaignPreview } from '../../src/lib/bindcraft2AuthoringApi';

let root: Root | undefined;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); });
async function mount(overrides: Partial<React.ComponentProps<typeof BindCraft2Campaign>> = {}) {
    const actions = { onNameChange: vi.fn(), onBack: vi.fn(), onPreview: vi.fn(), onLaunch: vi.fn(), onOpenLibrary: vi.fn() };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<BindCraft2Campaign name="my campaign" {...actions}
        generatorChooser={<button>Other generator</button>}
        requestedSettings={{ modality: ['VHH'], targets: [{ name: 'selected structure', target_path: 'inputs/a.pdb' }], max_trajectories: 12, number_of_final_designs: 0 }}
        preview={null} previewBusy={false} submitting={false} launchAvailable
        executionTarget={<div data-execution>Existing placement picker</div>}
        library={<div data-library>Existing campaign library</div>}
        {...overrides}>
        <section aria-label="Visual source preparation"><button>Target structure</button><button>Scaffold structure</button></section>
    </BindCraft2Campaign>));
    return actions;
}
const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === label)!;
const preview: BC2CampaignPreview = { preview_digest: 'native-digest', requested_settings: {}, effective_settings: { modality: ['VHH'], targets: [{ name: 'native target' }], max_trajectories: 25 } };

it('composes visual preparation, named campaign, generator, library and placement with styled actions', async () => {
    const actions = await mount();
    expect(document.querySelector('[aria-label="Visual source preparation"]')).not.toBeNull();
    expect(document.querySelector('[data-execution]')).not.toBeNull();
    expect(document.querySelector('[data-library]')).not.toBeNull();
    const review = document.querySelector('[aria-label="Campaign review and launch"]')!;
    expect(review.textContent).toContain('selected structure');
    expect(review.textContent).toContain('12');
    expect([...review.querySelectorAll('dd')].map(item => item.textContent)).toContain('0');
    expect(button('Preview native campaign').style.background).toBe('var(--accent-primary)');
    expect(button('Preview native campaign').style.color).toBe('var(--text-on-accent)');
    expect(button('Launch BindCraft2 campaign').disabled).toBe(true);
    await act(async () => { button('Preview native campaign').click(); button('Saved campaigns').click(); button('Save campaign draft').click(); });
    expect(actions.onPreview).toHaveBeenCalledOnce();
    expect(actions.onOpenLibrary).toHaveBeenCalledTimes(2);
    expect(actions.onLaunch).not.toHaveBeenCalled();
});

it('uses the existing preview contract without turning warning records into a new launch restriction', async () => {
    const actions = await mount({ preview: { ...preview, warnings: ['native note'], blockers: [{ message: 'reviewed source observation' }] } });
    expect(document.querySelector('[aria-label="Campaign review and launch"]')?.textContent).toContain('native target');
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')?.textContent).toContain('native note');
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')?.textContent).toContain('reviewed source observation');
    expect(button('Launch BindCraft2 campaign').disabled).toBe(false);
    await act(async () => button('Launch BindCraft2 campaign').click());
    expect(actions.onLaunch).toHaveBeenCalledOnce();
    const details = [...document.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === 'Full compiled configuration')!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain('native-digest');
});

it('preserves explicit scientific values in requested readback and leaves the primary form editable offline', async () => {
    const requestedSettings = { trajectory_only: false, binder_name: null, targets: [], losses: { induced_fit_interface: { params: { interface_mask: [0, 0.5, 1] } } } };
    await mount({ requestedSettings, launchAvailable: false });
    const details = [...document.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === 'Requested native settings')!;
    expect(JSON.parse(details.querySelector('pre')!.textContent!)).toEqual(requestedSettings);
    expect(details.open).toBe(false);
    expect(button('Preview native campaign').disabled).toBe(false);
    expect(button('Save campaign draft').disabled).toBe(false);
    expect(button('Launch BindCraft2 campaign')).toBeUndefined();
    expect(document.querySelector('[aria-label="Visual source preparation"]')).not.toBeNull();
});

it('edits the campaign name and exposes submission errors without replacing the workbench', async () => {
    const actions = await mount({ error: 'Native source error', previewBusy: true, preview });
    const input = document.querySelector<HTMLInputElement>('[aria-label="Draft name"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'named draft'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    expect(actions.onNameChange).toHaveBeenCalledWith('named draft');
    expect(document.querySelector('[role="alert"]')?.textContent).toBe('Native source error');
    expect(button('Compiling native preview…').disabled).toBe(true);
    expect(document.querySelector('[aria-label="Visual source preparation"]')).not.toBeNull();
});
