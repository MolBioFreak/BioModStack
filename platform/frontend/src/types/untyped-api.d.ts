import type { AxiosResponse } from 'axios';

declare global {
    /**
     * Dynamic payload surface for legacy API contracts that are still schema-first
     * on the backend but not yet fully narrowed in the frontend.
     *
     * New code should prefer a concrete interface or `unknown` plus a local guard.
     */
    type UntypedApiValue = AxiosResponse['data'];
}

export {};
