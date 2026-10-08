const inspectCustomSymbol = Symbol.for('nodejs.util.inspect.custom');

function stringify(value: unknown): string {
    if (typeof value === 'string') {
        return value;
    }

    if (typeof value === 'bigint') {
        return value.toString();
    }

    if (typeof value === 'function') {
        return `[Function ${value.name || 'anonymous'}]`;
    }

    if (typeof value === 'symbol') {
        return value.toString();
    }

    if (value && typeof value === 'object') {
        const customInspect = (value as Record<PropertyKey, unknown>)[inspectCustomSymbol];
        if (typeof customInspect === 'function') {
            try {
                return String(customInspect.call(value));
            } catch {
                // Fall through to generic serialization.
            }
        }

        const seen = new WeakSet<object>();
        try {
            return JSON.stringify(value, (_key, currentValue) => {
                if (typeof currentValue === 'bigint') {
                    return currentValue.toString();
                }
                if (typeof currentValue === 'function') {
                    return `[Function ${currentValue.name || 'anonymous'}]`;
                }
                if (typeof currentValue === 'symbol') {
                    return currentValue.toString();
                }
                if (currentValue && typeof currentValue === 'object') {
                    if (seen.has(currentValue)) {
                        return '[Circular]';
                    }
                    seen.add(currentValue);
                }
                return currentValue;
            }) ?? String(value);
        } catch {
            return String(value);
        }
    }

    return String(value);
}

type InspectFn = ((value: unknown, options?: unknown) => string) & { custom: symbol };

export const inspect = ((value: unknown) => stringify(value)) as InspectFn;
inspect.custom = inspectCustomSymbol;

export function format(template: unknown, ...args: unknown[]): string {
    if (typeof template !== 'string') {
        return [template, ...args].map((value) => inspect(value)).join(' ');
    }

    let argIndex = 0;
    const formatted = template.replace(/%[sdj%]/g, (token) => {
        if (token === '%%') {
            return '%';
        }

        if (argIndex >= args.length) {
            return token;
        }

        const value = args[argIndex++];
        if (token === '%s') {
            return String(value);
        }
        if (token === '%d') {
            return String(Number(value));
        }
        if (token === '%j') {
            return stringify(value);
        }
        return token;
    });

    if (argIndex >= args.length) {
        return formatted;
    }

    return `${formatted} ${args.slice(argIndex).map((value) => inspect(value)).join(' ')}`;
}

export function debuglog(_section: string): (...args: unknown[]) => void {
    return () => {};
}

export function deprecate<T extends (...args: never[]) => unknown>(fn: T, _message: string): T {
    return fn;
}

export const types = {};

const util = {
    debuglog,
    deprecate,
    format,
    inspect,
    types,
};

export default util;
