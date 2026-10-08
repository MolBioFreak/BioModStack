import { createContext, useContext, useRef, useState, type ReactNode, type RefObject } from 'react';
type Owner = { busyRef: RefObject<boolean>; busy: boolean; setBusy: (busy: boolean) => void };
const Context = createContext<Owner | null>(null);
/** One synchronous mutation owner across legacy drafts and structured methods.
 * Switching presentation/selected jobs never releases an in-flight mutation. */
export function BioXpWorkflowMutationProvider({ children }: { children: ReactNode }) {
    const busyRef = useRef(false);
    const [busy, setBusy] = useState(false);
    return <Context.Provider value={{ busyRef, busy, setBusy }}>{children}</Context.Provider>;
}
export function useBioXpWorkflowMutationOwner(): Owner {
    const shared = useContext(Context), busyRef = useRef(false);
    const [busy, setBusy] = useState(false);
    return shared ?? { busyRef, busy, setBusy };
}
