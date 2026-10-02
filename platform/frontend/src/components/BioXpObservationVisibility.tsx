import { useEffect, useState } from 'react';

/** Observation demand only: never connection authority or mutation admission. */
export function useBioXpDocumentVisible() {
    const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
    useEffect(() => {
        const update = () => setVisible(document.visibilityState !== 'hidden');
        document.addEventListener('visibilitychange', update);
        return () => document.removeEventListener('visibilitychange', update);
    }, []);
    return visible;
}
