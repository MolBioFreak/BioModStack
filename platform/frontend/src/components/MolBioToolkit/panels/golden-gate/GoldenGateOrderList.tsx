import type { SuggestGoldenGateProposal } from '../../../../lib/api';

const copy = (text: string) => { void navigator.clipboard?.writeText(text); };

/** FASTA text of everything to order, one record per bare part. */
export const orderFasta = (proposal: SuggestGoldenGateProposal): string => (proposal.prepared_fragments ?? [])
    .filter(r => r.order_sequence).map(r => `>${r.name} ${r.tailed_length ?? r.order_sequence!.length} bp\n${r.order_sequence}`).join('\n');
/** CSV text: name,length_bp,sequence. */
export const orderCsv = (proposal: SuggestGoldenGateProposal): string => ['name,length_bp,sequence', ...(proposal.prepared_fragments ?? [])
    .filter(r => r.order_sequence).map(r => `${r.name},${r.tailed_length ?? r.order_sequence!.length},${r.order_sequence}`)].join('\n');

/** What to order for one plan: bare parts get their sequence with Type IIS flanks; pre-configured parts are used as supplied. */
export function GoldenGateOrderList({ proposal }: { proposal: SuggestGoldenGateProposal }) {
    const rows = proposal.prepared_fragments ?? [];
    if (rows.length === 0 && !proposal.order_summary) return null;
    const summary = proposal.order_summary;
    const toOrder = rows.filter(r => r.order_sequence);
    const total = summary?.order_total_bp ?? toOrder.reduce((n, r) => n + (r.tailed_length ?? r.order_sequence!.length), 0);
    const method = summary?.method ?? rows.find(r => r.preparation !== 'donor')?.preparation ?? 'synthesis';
    return <div aria-label="Order list" className="space-y-1 rounded border border-slate-700 bg-slate-950/70 p-2">
        <div className="font-medium text-slate-100">Order list</div>
        <div className="text-slate-300">{toOrder.length} to order by {method} · total {total} bp{proposal.final_sequence ? ` · final product ${proposal.final_sequence.length} bp` : ''}{summary ? ` · ${summary.prepared_parts} used as supplied` : ''}</div>
        <ul className="space-y-1">{rows.map((r, i) => <li key={`${r.part_id}-${i}`} className="text-slate-300">
            {r.order_sequence
                ? <><span className="font-medium">{r.name}</span> · {r.tailed_length ?? r.order_sequence.length} bp{r.payload_length != null ? ` (${r.payload_length} bp payload)` : ''}
                    <button type="button" className="ml-2 text-cyan-300" onClick={() => copy(r.order_sequence!)}>Copy sequence for {r.name}</button>
                    <code className="block max-h-20 overflow-auto break-all text-slate-400">{r.order_sequence}</code>
                    {r.primers?.length > 0 && <div className="text-slate-400">Primers: {r.primers.join(', ')}</div>}</>
                : <><span className="font-medium">{r.name}</span> · used as supplied, nothing to order{r.left_overhang || r.right_overhang ? ` (${r.left_overhang ?? '—'} … ${r.right_overhang ?? '—'})` : ''}</>}
        </li>)}</ul>
        {toOrder.length > 0 && <div className="flex gap-2 pt-1">
            <button type="button" className="rounded border border-slate-600 px-2 py-0.5 text-cyan-300" onClick={() => copy(orderCsv(proposal))}>Copy all as CSV</button>
            <button type="button" className="rounded border border-slate-600 px-2 py-0.5 text-cyan-300" onClick={() => copy(orderFasta(proposal))}>Copy all as FASTA</button>
        </div>}
    </div>;
}
