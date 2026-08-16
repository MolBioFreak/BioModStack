import fs from 'node:fs';
import path from 'node:path';

export const DEFAULT_ZOOM_FACTOR = 1;
export const MIN_ZOOM_FACTOR = 0.8;
export const MAX_ZOOM_FACTOR = 1.5;
export const ZOOM_STEP = 0.1;
export const ZOOM_PRESETS = [0.8, 0.9, 1, 1.1, 1.25, 1.5] as const;

function roundZoomFactor(value: number): number {
  return Math.round(value * 100) / 100;
}

export function clampZoomFactor(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_ZOOM_FACTOR;
  }
  return roundZoomFactor(Math.min(MAX_ZOOM_FACTOR, Math.max(MIN_ZOOM_FACTOR, value)));
}

export function adjustZoomFactor(currentZoomFactor: number, deltaSteps: number): number {
  if (!Number.isFinite(deltaSteps) || deltaSteps === 0) {
    return clampZoomFactor(currentZoomFactor);
  }
  return clampZoomFactor(currentZoomFactor + (deltaSteps * ZOOM_STEP));
}

export function formatZoomPercentage(value: number): string {
  return `${Math.round(clampZoomFactor(value) * 100)}%`;
}

export function readPersistedZoomFactor(filePath: string): number {
  try {
    const payload = JSON.parse(fs.readFileSync(filePath, 'utf8')) as { zoomFactor?: number };
    return clampZoomFactor(payload.zoomFactor ?? DEFAULT_ZOOM_FACTOR);
  } catch {
    return DEFAULT_ZOOM_FACTOR;
  }
}

export function writePersistedZoomFactor(filePath: string, zoomFactor: number): void {
  const normalizedZoomFactor = clampZoomFactor(zoomFactor);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify({ zoomFactor: normalizedZoomFactor }, null, 2) + '\n', 'utf8');
}
