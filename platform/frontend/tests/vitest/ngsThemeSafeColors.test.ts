import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// Hardcoded Tailwind palette tones look fine on dark themes but disappear on the
// Light and Clean Light palettes. NGS components use the per-theme --success,
// --warning, --error and --accent-primary variables instead.
const SRC = join(__dirname, '..', '..', 'src', 'components');
const FILES = [
    join(SRC, 'NGSToolkit.tsx'),
    join(SRC, 'NanoporeTemplate.tsx'),
    ...readdirSync(join(SRC, 'ngs')).filter((name) => name.endsWith('.tsx')).map((name) => join(SRC, 'ngs', name)),
];
const PALETTE = /\b(?:text|bg|border)-(?:red|rose|amber|yellow|orange|emerald|green|cyan|sky|blue|slate|gray|zinc)-\d{2,3}(?:\/\d+)?\b/g;

describe('NGS theme-safe colours', () => {
    it.each(FILES)('%s uses theme variables instead of fixed palette tones', (file) => {
        const hits = readFileSync(file, 'utf8').match(PALETTE) ?? [];
        expect(hits).toEqual([]);
    });
});
