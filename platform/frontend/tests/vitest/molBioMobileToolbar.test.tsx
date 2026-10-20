import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MobileMolBioToolbar } from '../../src/components/MolBioToolkit/MobileMolBioToolbar';
import { MobileMolBioWorkspace } from '../../src/components/MolBioToolkit/MobileMolBioWorkspace';

describe('MobileMolBioToolbar', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
    });

    async function renderToolbar(surface: 'map' | 'sequence' | 'details' | 'digest' | 'qc' = 'map') {
        const onBack = vi.fn();
        const onOpenConstructs = vi.fn();
        const onSurfaceChange = vi.fn();
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        await act(async () => {
            root?.render(
                <MobileMolBioToolbar
                    constructName="PL931"
                    digestAvailable
                    qcAvailable
                    hasSequence
                    surface={surface}
                    onBack={onBack}
                    onOpenConstructs={onOpenConstructs}
                    onSurfaceChange={onSurfaceChange}
                />,
            );
        });
        return { container, onBack, onOpenConstructs, onSurfaceChange };
    }

    it('renders only the approved read-first mobile surfaces inside safe-area chrome', async () => {
        const rendered = await renderToolbar('map');
        const toolbar = rendered.container.querySelector<HTMLElement>('[data-molbio-mobile-toolbar="true"]');
        expect(toolbar).toBeTruthy();
        expect(toolbar?.className).not.toContain('overflow-x-auto');
        expect(rendered.container.querySelector('nav')?.className).toContain('grid-cols-5');
        expect(toolbar?.className).toContain('safe-area-inset-left');
        expect(toolbar?.className).toContain('safe-area-inset-right');
        expect(toolbar?.style.paddingTop).toContain('safe-area-inset-top');
        expect(toolbar?.style.paddingTop).toContain('0.75rem');

        const labels = [...rendered.container.querySelectorAll('button')].map((button) => button.textContent?.trim());
        expect(labels).toEqual(expect.arrayContaining(['Back', 'Constructs', 'Map', 'Sequence', 'Details', 'Digest', 'QC']));
        expect(labels.join(' ')).not.toMatch(/Edit|Assembly|Align|Auto-Annotate/u);
        for (const button of rendered.container.querySelectorAll('button')) {
            expect(button.className).toContain('min-h-12');
            expect(button.className).toContain('min-w-12');
        }
        expect(rendered.container.querySelector('button[aria-pressed="true"]')?.textContent).toContain('Map');
    });

    it('tracks the visible keyboard viewport and removes its listeners on exit', async () => {
        const previous = Object.getOwnPropertyDescriptor(window, 'visualViewport');
        const viewport = Object.assign(new EventTarget(), { height: 764, offsetTop: 0 });
        const remove = vi.spyOn(viewport, 'removeEventListener');
        Object.defineProperty(window, 'visualViewport', { configurable: true, value: viewport });
        try {
            container = document.createElement('div');
            document.body.appendChild(container);
            root = createRoot(container);
            await act(async () => root?.render(<MobileMolBioWorkspace
                constructName="PL931" digestIdentity="PL931" digestAvailable qcAvailable
                hasSequence constructPickerOpen={false} surface="map"
                onBack={vi.fn()} onOpenConstructs={vi.fn()} onSurfaceChange={vi.fn()}
                constructs={null} map={<div>map</div>} sequence={null} details={null} digest={null} qc={null}
            />));
            const workspace = container.querySelector<HTMLElement>('[data-molbio-mobile-layout]')!;
            expect(workspace.style.getPropertyValue('--molbio-viewport-height')).toBe('764px');
            viewport.height = 404;
            viewport.dispatchEvent(new Event('resize'));
            expect(workspace.style.getPropertyValue('--molbio-viewport-height')).toBe('404px');
            viewport.offsetTop = 32;
            viewport.dispatchEvent(new Event('scroll'));
            expect(workspace.style.getPropertyValue('--molbio-viewport-top')).toBe('32px');
            viewport.height = 764;
            viewport.offsetTop = 0;
            viewport.dispatchEvent(new Event('resize'));
            expect(workspace.style.getPropertyValue('--molbio-viewport-height')).toBe('764px');
            await act(async () => root?.unmount());
            root = undefined;
            expect(remove).toHaveBeenCalledWith('resize', expect.any(Function));
            expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function));
            expect(document.documentElement.classList.contains('bms-molbio-mobile-active')).toBe(false);
        } finally {
            if (previous) Object.defineProperty(window, 'visualViewport', previous);
            else Reflect.deleteProperty(window, 'visualViewport');
        }
    });

    it('publishes trusted toolbar actions through explicit callbacks', async () => {
        const rendered = await renderToolbar();
        const button = (label: string) => [...rendered.container.querySelectorAll('button')]
            .find((candidate) => candidate.textContent?.trim() === label);

        await act(async () => button('Digest')?.click());
        expect(rendered.onSurfaceChange).toHaveBeenCalledWith('digest');

        await act(async () => button('Constructs')?.click());
        expect(rendered.onOpenConstructs).toHaveBeenCalledTimes(1);

        await act(async () => button('Back')?.click());
        expect(rendered.onBack).toHaveBeenCalledTimes(1);
    });
});
