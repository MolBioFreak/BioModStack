// Display ordering only: never changes mutation ownership, queues or admission.
export function repairManualLatestRequest<T extends { submittedAt: number; status: string; requestOrder?: number }>(requests: readonly T[]): T | undefined {
    return requests.filter(request => request.status !== 'idle' && request.submittedAt > 0)
        .reduce<T | undefined>((latest, request) => !latest || (request.requestOrder ?? request.submittedAt) > (latest.requestOrder ?? latest.submittedAt) ? request : latest, undefined);
}
