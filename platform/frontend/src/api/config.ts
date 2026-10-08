/**
 * BioModStack Stats Toolkit API base.
 * Keeps the legacy /assay route and /api/assay-analytics endpoints for compatibility.
 * Same-origin path works in local Vite proxy, nginx /bms deployment, and Tailnet HTTPS.
 */
export const API_URL = '/api/assay-analytics';
export const VLM_URL = '/api/assay-analytics/vlm-disabled';
