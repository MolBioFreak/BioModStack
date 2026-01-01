import { useState, useEffect, useRef, useCallback } from 'react';

/**
 * A React 19 compatible replacement for react-sizeme.
 * Uses ResizeObserver to track element dimensions.
 */
export function useSize() {
    const [size, setSize] = useState({ width: 0, height: 0 });
    const ref = useRef(null);

    const handleResize = useCallback((entries) => {
        if (entries[0]) {
            const { width, height } = entries[0].contentRect;
            setSize({ width, height });
        }
    }, []);

    useEffect(() => {
        const element = ref.current;
        if (!element) return;

        const observer = new ResizeObserver(handleResize);
        observer.observe(element);

        // Initial size
        setSize({
            width: element.offsetWidth,
            height: element.offsetHeight
        });

        return () => observer.disconnect();
    }, [handleResize]);

    return { ref, size };
}

/**
 * HOC wrapper for compatibility with existing code expecting sizeMe() pattern.
 * Wraps the component and injects `size` prop.
 */
export function withSize() {
    return function (WrappedComponent) {
        return function SizedComponent({ key: _key, ...props }) {
            const { ref, size } = useSize();
            return (
                <div ref={ref} style={{ width: '100%', height: '100%' }}>
                    <WrappedComponent {...props} size={size} />
                </div>
            );
        };
    };
}


// Default export mimics sizeMe() API
export default withSize;
