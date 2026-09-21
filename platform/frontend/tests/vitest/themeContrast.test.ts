import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

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
        for (const decl of match[2].matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
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

describe('theme contrast', () => {
    it('defines the muted token for the default theme', () => {
        expect(rootTokens['--text-muted']).toBeTruthy();
    });

    it('keeps muted help copy readable on every surface, per theme cascade', () => {
        const failures: string[] = [];
        for (const block of blocks) {
            const tokens = { ...rootTokens, ...block.tokens };
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
            const tokens = { ...rootTokens, ...block.tokens };
            const muted = tokens['--text-muted'];
            const secondary = tokens['--text-secondary'];
            if (!muted || !secondary) continue;
            expect(muted, `${block.name} muted must differ from secondary`).not.toBe(secondary);
        }
    });
});
