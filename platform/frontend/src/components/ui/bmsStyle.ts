/*
 * Canonical BioModStack workbench styling tokens.
 *
 * Keep new dashboard/frontend work on these tracks instead of hand-rolling
 * panel radius/background/border recipes. Fullscreen/edge-to-edge viewers may
 * still use rounded-none border-0 intentionally.
 */

export const BMS_PANEL_SURFACE = 'rounded-xl border border-border-primary bg-surface-secondary/70 backdrop-blur-sm shadow-xl shadow-black/10';
export const BMS_PANEL_SURFACE_SOFT = 'rounded-xl border border-border-primary bg-surface-secondary/55 backdrop-blur-sm shadow-lg shadow-black/5';
export const BMS_PANEL_OVERFLOW = `${BMS_PANEL_SURFACE} overflow-hidden`;

export const BMS_CONTROL_GROUP = 'rounded-lg border border-border-secondary/70 bg-surface/60';
export const BMS_CONTROL = 'rounded-lg border border-border-secondary bg-surface-tertiary/80';
export const BMS_SMALL_CONTROL = 'rounded border border-border-secondary bg-surface-tertiary/80';

export const BMS_VIEWER_WELL = 'rounded-lg bg-surface/50';
export const BMS_FULLSCREEN_FLUSH = 'rounded-none border-0';
