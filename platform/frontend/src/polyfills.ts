import { Buffer } from 'buffer';

// @ts-ignore
if (typeof window !== 'undefined') {
    // @ts-ignore
    window.global = window;
    // @ts-ignore
    window.Buffer = Buffer;
    // @ts-ignore
    window.process = { env: {} };

    // Clean up corrupted localStorage entries from OVE/Teselagen
    // These cause "SyntaxError: JSON.parse: unexpected end of data" warnings
    try {
        const keysToCheck = Object.keys(localStorage).filter(
            key => key.startsWith('tg-') || key.startsWith('ve-') || key.startsWith('ove-')
        );
        keysToCheck.forEach(key => {
            const value = localStorage.getItem(key);
            if (value === '' || value === 'undefined' || value === 'null') {
                localStorage.removeItem(key);
            } else if (value) {
                try {
                    JSON.parse(value);
                } catch {
                    // Remove corrupted JSON entries
                    localStorage.removeItem(key);
                }
            }
        });
    } catch {
        // Ignore errors if localStorage is not available
    }
}
