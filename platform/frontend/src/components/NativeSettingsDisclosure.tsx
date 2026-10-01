import { useState, type ComponentPropsWithoutRef, type ReactNode } from 'react';

/** Defer field construction, never native discovery/default hydration. Once
 * inspected, keep the body mounted; drafts remain owned by the parent form. */
export function NativeSettingsDisclosure({ summary, children, open, ...props }: Omit<ComponentPropsWithoutRef<'details'>, 'children'> & {
    summary: ReactNode;
    children: () => ReactNode;
}) {
    const [visited, setVisited] = useState(Boolean(open));
    return <details {...props} open={open} onToggle={event => {
        if (event.currentTarget.open) setVisited(true);
        props.onToggle?.(event);
    }}>
        <summary>{summary}</summary>
        {(open || visited) && children()}
    </details>;
}
