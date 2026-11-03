// Plain user-facing wording shared by the alignment screens.
import { projectManagerErrorMessage } from '../../lib/projectManager'

/** Server detail when available, otherwise a short message; never a raw error object dump. */
export function errorText(error: unknown): string {
    return projectManagerErrorMessage(error).replace(/^Project Manager request failed/, 'Request failed')
}

const statusWords: Record<string, string> = {
    submitted: 'Submitted', queued: 'Queued', pending: 'Queued', running: 'Running…',
    completed: 'Done', succeeded: 'Done', success: 'Done', failed: 'Failed', cancelled: 'Cancelled', canceled: 'Cancelled',
}
export function statusText(status: string | null | undefined): string {
    if (!status) return 'Submitted'
    return statusWords[status] ?? status.replaceAll('_', ' ')
}

const roleWords: Record<string, string> = {
    source: 'Original file', alignment: 'Alignment', source_metadata: 'File notes', receipt: 'Run record',
    result_manifest: 'Result manifest', row_map: 'Row map', log: 'Engine log', input: 'Input', result: 'Result',
}
/** Download label for a result file: a readable role name plus its file name. */
export function downloadLabel(role: string, logicalPath?: string | null): string {
    const word = roleWords[role] ?? role.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase())
    return logicalPath ? `${word} (${logicalPath})` : word
}

/** Readable library availability; the normal state shows nothing. */
export function availabilityText(availability: string): string {
    return availability === 'available' ? '' : availability.replaceAll('_', ' ')
}
