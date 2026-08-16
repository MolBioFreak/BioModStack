export const exportMetricIdentity = (identity: object): Readonly<Record<string, unknown>> => (
    Object.fromEntries(Object.entries(identity).filter(([, value]) => value !== undefined))
);
