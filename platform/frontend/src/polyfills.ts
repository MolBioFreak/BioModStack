import { Buffer } from 'buffer';

type PolyfilledWindow = Window & typeof globalThis & {
    global: Window & typeof globalThis;
    Buffer: typeof Buffer;
    process: { env: Record<string, string> };
};

if (typeof window !== 'undefined') {
    const polyfilledWindow = window as PolyfilledWindow;
    polyfilledWindow.global = polyfilledWindow;
    polyfilledWindow.Buffer = Buffer;
    polyfilledWindow.process = { env: {} };

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
