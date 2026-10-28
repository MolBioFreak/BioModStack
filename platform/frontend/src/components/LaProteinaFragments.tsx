import { parseLaProteinaFragments } from '../lib/laProteinaMotifSelection';
/** One ordered native contig. Untouched tokens, empty segments and manual syntax
 * stay literal; opening this table neither normalizes nor applies source picks. */
export function LaProteinaFragments({ contig, onChange }: { contig: string; onChange: (value: string) => void }) {
    const fragments = parseLaProteinaFragments(contig);
    const replace = (index: number, token: string) => onChange(fragments.map((f, i) => i === index ? token : f.token).join('/'));
    return <fieldset className="space-y-2"><legend>Ordered motif fragments and linkers</legend>
        {fragments.map((fragment, i) => <div key={i} className="flex flex-wrap gap-2 items-end">
            <span>Fragment {i + 1} · {fragment.kind === 'length' ? 'generated length' : fragment.kind === 'motif' ? 'source residues' : 'native token'}</span>
            {fragment.kind === 'native' ? <label>Native token<input aria-label={`La fragment ${i + 1} native token`} value={fragment.token} onChange={e => replace(i, e.target.value)} /></label> : <>
                {fragment.kind === 'motif' && <label>Chain<input aria-label={`La fragment ${i + 1} chain`} value={fragment.chain} onChange={e => replace(i, `${e.target.value}${fragment.start}${fragment.end === undefined ? '' : `-${fragment.end}`}`)} /></label>}
                <label>{fragment.kind === 'length' ? 'Length / minimum' : 'First residue'}<input aria-label={`La fragment ${i + 1} start`} type="number" value={fragment.start} onChange={e => replace(i, `${fragment.chain ?? ''}${e.target.value}${fragment.end === undefined ? '' : `-${fragment.end}`}`)} /></label>
                {fragment.end !== undefined && <label>{fragment.kind === 'length' ? 'Maximum length' : 'Last residue'}<input aria-label={`La fragment ${i + 1} end`} type="number" value={fragment.end} onChange={e => replace(i, `${fragment.chain ?? ''}${fragment.start}-${e.target.value}`)} /></label>}
            </>}
            {i > 0 && <button type="button" onClick={() => { const tokens = fragments.map(f => f.token); [tokens[i - 1], tokens[i]] = [tokens[i], tokens[i - 1]]; onChange(tokens.join('/')); }}>Move fragment {i + 1} up</button>}
            <button type="button" onClick={() => onChange(fragments.filter((_, j) => i !== j).map(f => f.token).join('/'))}>Remove fragment {i + 1}</button>
        </div>)}
        <button type="button" onClick={() => onChange(contig === '' ? '0' : `${contig}/0`)}>Add exact generated length</button>
        <button type="button" onClick={() => onChange(contig === '' ? '0-0' : `${contig}/0-0`)}>Add generated length range</button>
        <button type="button" onClick={() => onChange(contig === '' ? 'A1' : `${contig}/A1`)}>Add source fragment</button>
        <p className="text-xs">Only explicit edits change the contig. Motif-only parsing consumes each retained chain once; this table does not renumber residues or replace native segment order.</p>
    </fieldset>;
}
