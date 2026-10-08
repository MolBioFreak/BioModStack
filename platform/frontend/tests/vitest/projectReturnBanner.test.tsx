import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { ProjectReturnBanner } from '../../src/components/project-manager/ProjectReturnBanner';

describe('ProjectReturnBanner', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
    });

    async function renderAt(url: string) {
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        await act(async () => {
            root?.render(
                <MemoryRouter initialEntries={[url]}>
                    <ProjectReturnBanner />
                </MemoryRouter>,
            );
        });
        return container;
    }

    it('restores the exact verified Project focus and selection context', async () => {
        const returnUri = '/projects/project-1?focus=global-1&selected=external_entity_receipt%3Areceipt-9';
        const rendered = await renderAt(`/designs/job-9?return_uri=${encodeURIComponent(returnUri)}`);
        const link = rendered.querySelector<HTMLAnchorElement>('a[aria-label="Return to Project context"]');
        expect(link?.getAttribute('href')).toBe(returnUri);
        expect(link?.textContent).toContain('Return to Project');
    });

    it('fails closed for external and incomplete return locations', async () => {
        const rendered = await renderAt(`/designs/job-9?return_uri=${encodeURIComponent('https://evil.example/projects/project-1?focus=x&selected=y')}`);
        expect(rendered.querySelector('a')).toBeNull();

        await act(async () => {
            root?.render(
                <MemoryRouter initialEntries={['/designs/job-9?return_uri=%2Fprojects%2Fproject-1']}>
                    <ProjectReturnBanner />
                </MemoryRouter>,
            );
        });
        expect(rendered.querySelector('a')).toBeNull();
    });
});
