import React from 'react';

export type BindCraft2Stage = 'trajectory' | 'draw' | 'retained' | 'attempt' | 'document';
export interface BindCraft2NativePage {
  schema: 'bindcraft2.native-readback.v1';
  arm: string | null;
  stage: BindCraft2Stage;
  offset: number;
  limit: number;
  total: number;
  accounting: Record<string, number | null>;
  arms: { name: string | null; accounting: Record<string, number | null> }[];
  metadata: Record<string, unknown> | null;
  rows: Record<string, unknown>[];
}

/** Presentation only: parent supplies verified, authorized pages and navigation. */
export function BindCraft2NativeResults({ page, onPage }: {
  page: BindCraft2NativePage;
  onPage: (query: { arm: string | null; stage: BindCraft2Stage; offset: number; limit: number }) => void;
}) {
  const change = (arm: string | null, stage: BindCraft2Stage, offset = 0) =>
    onPage({ arm, stage, offset, limit: page.limit });
  const display = (value: unknown): string => {
    if (value === null || value === undefined || value === '') return 'Unknown / not emitted';
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  };
  return <section aria-label="BindCraft2 native results">
    <h2>BindCraft2 native campaign</h2>
    <p>Native rows and metrics are shown without a cross-model score or inferred structure state.</p>
    <label>Campaign arm <select aria-label="Campaign arm" value={page.arm ?? ''}
      onChange={e => change(e.target.value || null, page.stage)}>
      {page.arms.map(a => <option key={a.name ?? ''} value={a.name ?? ''}>{a.name ?? 'Main campaign'}</option>)}
    </select></label>
    <dl>{Object.entries(page.accounting).map(([key, value]) =>
      <React.Fragment key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{display(value)}</dd></React.Fragment>)}</dl>
    <label>Native records <select aria-label="Native records" value={page.stage}
      onChange={e => change(page.arm, e.target.value as BindCraft2Stage)}>
      {(['trajectory', 'draw', 'retained', 'attempt', 'document'] as const).map(stage =>
        <option key={stage} value={stage}>{stage}</option>)}
    </select></label>
    <p>{page.total} records; showing {page.rows.length} from {page.offset + 1}</p>
    <div className="overflow-x-auto"><table><thead><tr><th>Identity</th><th>Native details</th></tr></thead>
      <tbody>{page.rows.map((row, index) => <tr key={`${page.arm ?? ''}:${page.stage}:${page.offset + index}`}>
        <th scope="row">{display(row.design ?? row.path)}</th>
        <td><dl>{Object.entries(row).filter(([key]) => key !== 'design' && key !== 'path').map(([key, value]) =>
          <React.Fragment key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{display(value)}</dd></React.Fragment>)}</dl></td>
      </tr>)}</tbody></table></div>
    <button type="button" disabled={page.offset === 0} onClick={() => change(page.arm, page.stage, Math.max(0, page.offset - page.limit))}>Previous</button>
    <button type="button" disabled={page.offset + page.limit >= page.total} onClick={() => change(page.arm, page.stage, page.offset + page.limit)}>Next</button>
  </section>;
}
