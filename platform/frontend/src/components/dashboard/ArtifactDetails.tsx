import { useState, type ReactNode } from 'react';

/** File inventories are optional detail, not the worker's primary status. */
export function ArtifactDetails({ label, count, children }: {
  label: string;
  count: number;
  children: () => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return <details className="text-xs" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary className="cursor-pointer rounded py-1 font-medium focus-visible:outline focus-visible:outline-2">
      {label} ({count.toLocaleString()})
    </summary>
    {expanded && <div className="mt-2 max-h-64 overflow-auto overscroll-contain" tabIndex={0} role="region" aria-label={label}>
      {children()}
    </div>}
  </details>;
}
