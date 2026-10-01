import { useEffect, useRef, useState, type ReactNode } from 'react';

/** File inventories are optional detail, not the worker's primary status. */
export function ArtifactDetails({ label, count, children }: {
  label: string;
  count: number;
  children: () => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const element = useRef<HTMLDetailsElement>(null);
  const [ancestorsOpen, setAncestorsOpen] = useState(false);
  useEffect(() => {
    const ancestors: HTMLDetailsElement[] = [];
    for (let parent = element.current?.parentElement; parent; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement) ancestors.push(parent);
    }
    const update = () => setAncestorsOpen(ancestors.every(parent => parent.open));
    update();
    ancestors.forEach(parent => parent.addEventListener('toggle', update));
    return () => ancestors.forEach(parent => parent.removeEventListener('toggle', update));
  }, []);
  return <details ref={element} className="text-xs" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary className="cursor-pointer rounded py-1 font-medium focus-visible:outline focus-visible:outline-2">
      {label} ({count.toLocaleString()})
    </summary>
    {expanded && ancestorsOpen && <div className="mt-2 max-h-64 overflow-auto overscroll-contain" tabIndex={0} role="region" aria-label={label}>
      {children()}
    </div>}
  </details>;
}
