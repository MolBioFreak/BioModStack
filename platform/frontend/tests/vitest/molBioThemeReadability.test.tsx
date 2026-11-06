import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { SequenceViewer, type ColorPaletteName } from '../../src/components/MolBioToolkit/SequenceViewer';
import { VisibilityPanel } from '../../src/components/MolBioToolkit/VisibilityPanel';
import { COLOR_PALETTES, EMPTY_SEQUENCE } from '../../src/components/MolBioToolkit/sequenceViewerConstants';
import { textContrast } from '../../src/lib/readableColors';
// Render the actual locked SeqViz, giving jsdom an explicit viewport.
vi.mock('seqviz', async (original) => { const actual = await original<typeof import('seqviz')>(); return { ...actual, SeqViz: (props: React.ComponentProps<typeof actual.SeqViz>) => <actual.SeqViz {...props} disableExternalFonts testSize={{ width: 1000, height: 700 }} /> }; });
const css = readFileSync(resolve(__dirname, '../../src/index.css'), 'utf8');
const themes = [...css.matchAll(/\[data-theme="([^"]+)"\]\s*\{([\s\S]*?)\n\}/g)].flatMap((match) => {
    const background = match[2].match(/--bg-secondary:\s*(#[a-f0-9]{6})/i)?.[1];
    return background ? [{ name: match[1], background }] : [];
});
function Harness() {
    const [palette, setPalette] = useState<ColorPaletteName>('classic');
    const [view, setView] = useState<'linear'|'circular'|'both'>('both');
    const [visibility, setVisibility] = useState({ features: true, primers: true, cutsites: false, translations: false, reverseComplement: true });
    return <><VisibilityPanel colorPalette={palette} onColorPaletteChange={setPalette} visibility={visibility} onChange={(key)=>setVisibility((old)=>({...old,[key]:!old[key]}))} />{(['linear','circular','both'] as const).map((mode)=><button key={mode} onClick={()=>setView(mode)}>{mode}</button>)}<SequenceViewer sequenceData={{ ...EMPTY_SEQUENCE, name: 'Readability QA', sequence: 'ACGT'.repeat(100), circular: true, features: [{ id: 'feature', name: 'QA marker', type: 'misc_feature', start: 10, end: 80, strand: 1, color: '#ffff00' }] }} visibility={visibility} colorPalette={palette} viewMode={view} selectedEnzymes={[]} /></>;
}
it('dogfoods all theme/palette selections in the native renderer and switches linear, circular, both and complement visibility', async () => {
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    const previous = document.documentElement.style.getPropertyValue('--bg-secondary');
    const previousTheme = document.documentElement.getAttribute('data-theme');
    try {
        await act(()=>root.render(<Harness />));
        const select = host.querySelector('select')!;
        expect(themes).toHaveLength(14);
        for (const theme of themes) {
            await act(async()=>{document.documentElement.dataset.theme=theme.name; document.documentElement.style.setProperty('--bg-secondary',theme.background); await new Promise((resolve)=>setTimeout(resolve,0));});
            for (const palette of Object.keys(COLOR_PALETTES)) {
                await act(()=>{select.value=palette;select.dispatchEvent(new Event('change',{bubbles:true}));});
                const bases=[...host.querySelectorAll('.la-vz-seqblock tspan[fill]')];
                expect(bases.length, `${theme.name}/${palette} native bases`).toBeGreaterThan(0);
                for(const base of bases) expect(textContrast(base.getAttribute('fill')!,theme.background),`${theme.name}/${palette}/${base.textContent}`).toBeGreaterThanOrEqual(6.5);
            }
        }
        for(const mode of ['linear','circular','both']) {
            await act(()=>[...host.querySelectorAll('button')].find((button)=>button.textContent===mode)!.click());
            expect(Boolean(host.querySelector('[data-testid="la-vz-viewer-linear"]'))).toBe(mode!=='circular');
            expect(Boolean(host.querySelector('.la-vz-viewer-circular'))).toBe(mode!=='linear');
        }
        const complement=[...host.querySelectorAll('label')].find((label)=>label.textContent?.includes('Complement Strand'))!.querySelector('input')!;
        await act(()=>complement.click());
        expect(complement.checked).toBe(false);
        await act(()=>complement.click()); expect(complement.checked).toBe(true);
    } finally {
        await act(()=>root.unmount()); host.remove();
        document.documentElement.style.setProperty('--bg-secondary',previous);
        if(previousTheme===null) document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme',previousTheme);
    }
}, 30000);
