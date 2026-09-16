import { useEffect, useMemo, useRef, type DependencyList } from 'react';

/** A new input generation owns completions, including A → B → A edits. */
export function useInputOwnership(inputs: DependencyList) {
    const token = useMemo(() => ({}), inputs);
    const current = useRef<object | null>(token);
    current.current = token;
    useEffect(() => {
        current.current = token;
        return () => { current.current = null; };
    }, [token]);
    return { token, isCurrent: () => current.current === token };
}
