import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { readableTextColor } from '../../src/lib/readableColors';
import { COLOR_PALETTES } from '../../src/components/MolBioToolkit/sequenceViewerConstants';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DNASequenceProjectWorkspace } from '../../src/components/project-manager/DNASequenceProjectWorkspace';
import type { ProjectHubReadModel } from '../../src/lib/api';

/**
 * Theme contrast guard.
 *
 * The muted token is help-copy text (12-13px), so it has to meet the 4.5:1 minimum against every
 * surface it can land on. It previously shipped at 3.1:1 on the deepest control surface and 2.1:1
 * in the light themes, which is why guidance lines read as a smear on the dark panels.
 */

function channel(v: number): number {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

function luminance(hex: string): number {
    const body = hex.trim().replace('#', '');
    const [r, g, b] = [0, 2, 4].map(offset => parseInt(body.slice(offset, offset + 2), 16));
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(foreground: string, background: string): number {
    const a = luminance(foreground);
    const b = luminance(background);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

const SURFACES = ['--bg-primary', '--bg-secondary', '--bg-tertiary', '--surface-control', '--surface-control-strong'];

function parseBlocks(css: string): { name: string; tokens: Record<string, string> }[] {
    const blocks: { name: string; tokens: Record<string, string> }[] = [];
    const re = /(?:^|\n)((?:\[data-theme="[^"]+"\]|:root)(?:,\s*\n\[data-theme="[^"]+"\])*)\s*\{([\s\S]*?)\n\}/g;
    let match: RegExpExecArray | null;
    while ((match = re.exec(css))) {
        const tokens: Record<string, string> = {};
        for (const decl of match[2].matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6}|var\(--[a-z0-9-]+\))\s*;/g)) {
            tokens[decl[1]] = decl[2].toLowerCase();
        }
        for (const selector of match[1].split(',')) {
            const name = selector.trim();
            if (name) blocks.push({ name, tokens });
        }
    }
    return blocks;
}

const css = readFileSync(resolve(__dirname, '../../src/index.css'), 'utf8');
const blocks = parseBlocks(css);
const rootTokens = blocks.find(block => block.name === ':root')?.tokens ?? {};

function themeTokens(block: (typeof blocks)[number]): Record<string, string> {
    const tokens = { ...rootTokens, ...block.tokens };
    const resolveToken = (name: string, seen = new Set<string>()): string => {
        if (seen.has(name)) throw new Error(`Circular theme token: ${name}`);
        seen.add(name);
        const value = tokens[name];
        const alias = value?.match(/^var\((--[a-z0-9-]+)\)$/)?.[1];
        return alias ? resolveToken(alias, seen) : value;
    };
    return Object.fromEntries(Object.keys(tokens).map((name) => [name, resolveToken(name)]));
}

function mix(a: string, b: string, fraction: number): string {
    return '#' + [1, 3, 5].map((offset) => Math.round(
        parseInt(a.slice(offset, offset + 2), 16) * fraction
        + parseInt(b.slice(offset, offset + 2), 16) * (1 - fraction),
    ).toString(16).padStart(2, '0')).join('');
}

describe('theme contrast', () => {
    it('defines the muted token for the default theme', () => {
        expect(rootTokens['--text-muted']).toBeTruthy();
    });

    it('keeps muted help copy readable on every surface, per theme cascade', () => {
        const failures: string[] = [];
        for (const block of blocks) {
            const tokens = themeTokens(block);
            const muted = tokens['--text-muted'];
            if (!muted) continue;
            for (const surface of SURFACES) {
                const background = tokens[surface];
                if (!background) continue;
                const ratio = contrastRatio(muted, background);
                if (ratio < 4.5) {
                    failures.push(`${block.name} ${muted} on ${surface} ${background} = ${ratio.toFixed(2)}:1`);
                }
            }
        }
        expect(failures).toEqual([]);
    });

    it('keeps muted distinct from secondary so hierarchy is preserved', () => {
        for (const block of blocks) {
            const tokens = themeTokens(block);
            const muted = tokens['--text-muted'];
            const secondary = tokens['--text-secondary'];
            if (!muted || !secondary) continue;
            expect(muted, `${block.name} muted must differ from secondary`).not.toBe(secondary);
        }
    });
});


describe('native MolBio and Project readability', () => {
    it('keeps real Project card links readable against the global link rules in all themes and action states', async () => {
        const cardCss = readFileSync(resolve(__dirname, '../../src/components/project-manager/DNASequenceProjectWorkspace.css'), 'utf8');
        const globalLinks = css.match(/\/\* Ensure links are visible \*\/[\s\S]*?(?=\/\* Smooth transitions)/)?.[0];
        expect(globalLinks).toBeTruthy();
        const model = {
            project: {plasmid_count: 2, add_plasmid_href: '/designer'},
            plasmids: ['Short name', 'A much longer reference sequence name that used to wrap the buttons onto another row'].map((name, i) => ({
                sequence_id: `seq-${i}`, name, reopen_href: `/designer?id=${i}`, description: 'Imported reference',
                revision_number: 1, length_bp: 1000, feature_count: 4, topology: 'circular',
            })),
            sequence_data: {items: [], launcher_href: '/ngs'}, experiments: [], results: [], activity: [], identity: {},
        } as unknown as ProjectHubReadModel;
        const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
        const styles = document.createElement('style'); document.head.append(styles);
        const client = new QueryClient();
        const rgbHex = (rgb: string) => '#' + (rgb.match(/\d+/g) ?? []).slice(0, 3).map(n => Number(n).toString(16).padStart(2, '0')).join('');
        try {
            await act(() => root.render(createElement(QueryClientProvider, {client}, createElement(MemoryRouter, {}, createElement(DNASequenceProjectWorkspace, {model})))));
            const links = [...host.querySelectorAll<HTMLAnchorElement>('a')].filter(link => ['Open latest', 'Add DNA sequence'].includes(link.textContent ?? ''));
            expect(links).toHaveLength(3);
            for (const block of blocks.filter(row => row.name !== ':root')) {
                const tokens = themeTokens(block);
                // jsdom has no full cascade-layer/custom-property engine. Resolve only
                // the theme variables, preserving the actual component/global selectors.
                styles.textContent = `${globalLinks}\n${cardCss}`
                    .replace(/var\((--[a-z-]+)\)/g, (_, name) => tokens[name])
                    .replace(/:hover/g, '[data-test-hover]').replace(/:focus-visible/g, '[data-test-focus]');
                for (const link of links) for (const state of ['normal', 'hover', 'focus']) {
                    link.toggleAttribute('data-test-hover', state === 'hover');
                    link.toggleAttribute('data-test-focus', state === 'focus');
                    const computed = getComputedStyle(link);
                    expect(contrastRatio(rgbHex(computed.color), rgbHex(computed.backgroundColor)), `${block.name}/${state}`).toBeGreaterThanOrEqual(4.5);
                    expect(rgbHex(computed.color)).toBe(tokens['--text-on-accent']);
                }
                for (const badge of host.querySelectorAll('.dna-project-revision')) {
                    const computed = getComputedStyle(badge);
                    expect(contrastRatio(rgbHex(computed.color), rgbHex(computed.backgroundColor)), `${block.name} revision badge`).toBeGreaterThanOrEqual(4.5);
                }
            }
            const headers = [...host.querySelectorAll('.dna-project-card-header')];
            expect(headers).toHaveLength(2);
            for (const header of headers) {
                expect(getComputedStyle(header).flexDirection).toBe('column');
                expect(header.lastElementChild?.classList.contains('dna-project-card-actions')).toBe(true);
            }
        } finally { await act(() => root.unmount()); host.remove(); styles.remove(); client.clear(); }
    });
    it('keeps action and status text readable on every theme surface', () => {
        for (const block of blocks) {
            const tokens = themeTokens(block);
            for (const token of ['--text-action', '--text-success', '--text-warning', '--text-error']) {
                expect(tokens[token], `${block.name} ${token}`).toBeTruthy();
                for (const surface of [...SURFACES, '--card-bg', '--card-hover']) if (tokens[surface])
                    expect(contrastRatio(tokens[token], tokens[surface]), `${block.name} ${token} on ${surface}`).toBeGreaterThanOrEqual(4.5);
            }
            expect(contrastRatio(tokens['--text-on-accent'], tokens['--accent-primary']), block.name).toBeGreaterThanOrEqual(4.5);
        }
    });
    it('keeps every nucleotide palette readable in all 14 themes, including the selected region', () => {
        for (const block of blocks) {
            const tokens = themeTokens(block);
            const background = tokens['--bg-secondary'];
            const foreground = tokens['--text-primary'];
            const selected = '#' + [1,3,5].map((offset) => Math.round(parseInt(background.slice(offset, offset+2),16)*.92 + parseInt(foreground.slice(offset, offset+2),16)*.08).toString(16).padStart(2,'0')).join('');
            for (const [palette, definition] of Object.entries(COLOR_PALETTES)) for (const [base, color] of Object.entries(definition.colors)) if (color) {
                const adjusted = readableTextColor(color, background, 6.5);
                for (const surface of [background, selected]) expect(contrastRatio(adjusted, surface), `${block.name} ${palette}/${base} on ${surface}`).toBeGreaterThanOrEqual(4.5);
            }
        }
    });
});

describe('NGS themed surfaces', () => {
    it('covers every selectable theme and resolves inherited surfaces against that theme', () => {
        expect(blocks.filter(block => block.name !== ':root')).toHaveLength(14);
        for (const block of blocks) {
            const tokens = themeTokens(block);
            for (const surface of ['--surface-page', '--surface-card', '--surface-control', '--surface-plot']) {
                expect(tokens[surface], `${block.name} ${surface}`).toMatch(/^#[0-9a-f]{6}$/);
            }
            if (['[data-theme="light"]', '[data-theme="clean_light"]', '[data-theme="desert"]'].includes(block.name)) {
                expect(luminance(tokens['--surface-plot'])).toBeGreaterThan(.5);
                expect(luminance(tokens['--surface-card'])).toBeGreaterThan(.5);
            }
        }
    });
    it('keeps NGS labels and selected workflow descriptions readable across all themes', () => {
        const failures: string[] = [];
        for (const block of blocks) {
            const tokens = themeTokens(block);
            const selected = mix(tokens['--accent-primary'], tokens['--bg-secondary'], .1);
            for (const foreground of ['--text-primary', '--text-secondary', '--text-muted']) {
                for (const background of [tokens['--bg-primary'], tokens['--bg-secondary'], tokens['--bg-tertiary'], selected]) {
                    const ratio = contrastRatio(tokens[foreground], background);
                    if (ratio < 4.5) failures.push(`${block.name} ${foreground} on ${background}: ${ratio.toFixed(2)}`);
                }
            }
        }
        expect(failures).toEqual([]);
    });
});
