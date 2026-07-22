import type { ViewerSnapshotBindingV2 } from '../contracts/m6Reproducibility.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from '../contracts/viewerResults.js';

export const MOVIE_EXPORT_LIMITS = Object.freeze({
    maxWidth: 1920,
    maxHeight: 1080,
    maxFps: 60,
    maxDurationSeconds: 60,
    maxFrames: 3600,
    maxBitrate: 12_000_000,
    maxEstimatedBytes: 128 * 1024 * 1024,
});

export type MovieSourceKind = 'coordinate_trajectory' | 'interpolated_morph';
export type MovieFrame =
    | {
        readonly kind: 'coordinate_trajectory';
        readonly trajectoryId: string;
        readonly replica: number;
        readonly displayFrame: number;
        readonly sourceFrame: number;
        readonly timePs?: number;
        readonly step?: number;
    }
    | {
        readonly kind: 'interpolated_morph';
        readonly morphId: string;
        readonly morphStep: number;
        readonly semanticWarning: 'visual_interpolation_not_physical_trajectory';
    };

export interface AuthoritativeFrameStepper {
    readonly sourceKind: MovieSourceKind;
    readonly provenanceRef: string;
    readonly sourceBindings: readonly ViewerSnapshotBindingV2[];
    readonly frames: readonly MovieFrame[];
    apply(frame: MovieFrame, signal: AbortSignal): Promise<ViewerResult<void>>;
}

export interface MovieExportRequestV1 {
    readonly fps: number;
    readonly bitrate: number;
    readonly outputFileName: string;
    readonly codec: 'video/webm;codecs=vp9';
    readonly sourceSnapshotSha256: string;
    /** Set only after the real-browser VP9/ffprobe acceptance gate for this runtime. */
    readonly capabilityProven: boolean;
}

export interface GovernedWebMResult {
    readonly blob: Blob;
    readonly completedFrames: number;
    readonly semanticWarnings: readonly string[];
    readonly sourceFrameRange: { readonly first: number; readonly last: number } | null;
    readonly width: number;
    readonly height: number;
    readonly fps: number;
    readonly bitrate: number;
    readonly codec: 'video/webm;codecs=vp9';
}

export const movieSemanticWarnings = (sourceKind: MovieSourceKind): readonly string[] => (
    sourceKind === 'interpolated_morph' ? ['visual_interpolation_not_physical_trajectory'] : []
);

const validSource = (stepper: AuthoritativeFrameStepper): boolean => {
    if (!stepper.provenanceRef.trim() || stepper.frames.length < 1 || stepper.frames.length > MOVIE_EXPORT_LIMITS.maxFrames || stepper.sourceBindings.length < 1) return false;
    if (stepper.sourceKind === 'coordinate_trajectory') {
        return stepper.frames.every((frame, index) => frame.kind === 'coordinate_trajectory'
            && frame.displayFrame === index && frame.sourceFrame >= 0 && Number.isInteger(frame.sourceFrame)
            && Number.isInteger(frame.replica) && frame.replica >= 0);
    }
    return stepper.frames.every((frame, index) => frame.kind === 'interpolated_morph'
        && frame.morphStep === index && frame.semanticWarning === 'visual_interpolation_not_physical_trajectory');
};

export const validateMovieExportRequest = (
    request: MovieExportRequestV1,
    stepper: AuthoritativeFrameStepper,
    canvas: { readonly width: number; readonly height: number },
): ViewerResult<MovieExportRequestV1> => {
    const duration = stepper.frames.length / request.fps;
    const estimatedBytes = request.bitrate * duration / 8;
    if (!request.capabilityProven) return viewerUnsupported('WebM/VP9 export lacks the required real-browser/ffprobe capability proof', 'export-webm-v1');
    if (!validSource(stepper)) return viewerUnsupported('Movie source frames, provenance, or bindings are invalid', 'export-webm-v1');
    if (!Number.isInteger(request.fps) || request.fps < 1 || request.fps > MOVIE_EXPORT_LIMITS.maxFps
        || !Number.isInteger(request.bitrate) || request.bitrate < 1 || request.bitrate > MOVIE_EXPORT_LIMITS.maxBitrate
        || !Number.isInteger(canvas.width) || !Number.isInteger(canvas.height) || canvas.width < 1 || canvas.height < 1
        || canvas.width > MOVIE_EXPORT_LIMITS.maxWidth || canvas.height > MOVIE_EXPORT_LIMITS.maxHeight
        || duration > MOVIE_EXPORT_LIMITS.maxDurationSeconds || estimatedBytes > MOVIE_EXPORT_LIMITS.maxEstimatedBytes) {
        return viewerUnsupported('Movie request exceeds governed WebM export limits', 'export-webm-v1');
    }
    if (request.codec !== 'video/webm;codecs=vp9' || !/^[0-9a-f]{64}$/.test(request.sourceSnapshotSha256)
        || !/^[^/\\\u0000-\u001f]+\.webm$/i.test(request.outputFileName)) {
        return viewerUnsupported('Movie codec, snapshot hash, or output basename is unsupported', 'export-webm-v1');
    }
    return viewerOk(request);
};

export const supportsGovernedWebMExport = (): boolean => (
    Boolean(globalThis.crypto?.subtle)
    && typeof MediaRecorder !== 'undefined'
    && typeof MediaRecorder.isTypeSupported === 'function'
    && MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
    && typeof HTMLCanvasElement !== 'undefined'
    && typeof HTMLCanvasElement.prototype.captureStream === 'function'
);

const waitUntil = (deadline: number, signal: AbortSignal): Promise<void> => new Promise((resolve, reject) => {
    const remaining = Math.max(0, deadline - performance.now());
    const timer = window.setTimeout(resolve, remaining);
    signal.addEventListener('abort', () => {
        window.clearTimeout(timer);
        reject(new DOMException('Movie export cancelled', 'AbortError'));
    }, { once: true });
});

export const encodeGovernedWebM = async (
    canvas: HTMLCanvasElement,
    stepper: AuthoritativeFrameStepper,
    request: MovieExportRequestV1,
    signal: AbortSignal,
    onProgress?: (completedFrames: number) => void,
): Promise<ViewerResult<GovernedWebMResult>> => {
    const admitted = validateMovieExportRequest(request, stepper, { width: canvas.width, height: canvas.height });
    if (admitted.status !== 'ok') return admitted as ViewerResult<never>;
    if (!supportsGovernedWebMExport()) return viewerUnsupported('WebM/VP9 canvas recording is unavailable', 'export-webm-v1');
    if (signal.aborted) return { status: 'cancelled', reason: 'Movie export was cancelled before encoder allocation' };

    let stream: MediaStream | undefined;
    let recorder: MediaRecorder | undefined;
    let stopped: Promise<void> | undefined;
    const chunks: Blob[] = [];
    let chunkBytes = 0;
    let encoderLimitExceeded = false;
    let completedFrames = 0;
    try {
        stream = canvas.captureStream(0);
        const tracks = stream.getVideoTracks();
        const track = tracks[0] as (MediaStreamTrack & { requestFrame?: () => void }) | undefined;
        if (tracks.length !== 1 || typeof track?.requestFrame !== 'function') {
            return viewerUnsupported('Manual one-track canvas frame capture is unavailable', 'export-webm-v1');
        }
        recorder = new MediaRecorder(stream, { mimeType: request.codec, videoBitsPerSecond: request.bitrate });
        if (!recorder.mimeType.toLowerCase().includes('vp9')) return viewerUnsupported('Browser did not retain the exact VP9 codec', 'export-webm-v1');
        stopped = new Promise<void>((resolve, reject) => {
            recorder!.addEventListener('dataavailable', (event) => {
                if (event.data.size <= 0) return;
                chunkBytes += event.data.size;
                if (chunkBytes > MOVIE_EXPORT_LIMITS.maxEstimatedBytes) {
                    encoderLimitExceeded = true;
                    if (recorder?.state !== 'inactive') recorder?.stop();
                    return;
                }
                chunks.push(event.data);
            });
            recorder!.addEventListener('stop', () => resolve(), { once: true });
            recorder!.addEventListener('error', () => reject(new Error('WebM encoder failed')), { once: true });
        });
        recorder.start(1000);
        const frameInterval = 1000 / request.fps;
        const startedAt = performance.now();
        for (const [index, frame] of stepper.frames.entries()) {
            if (encoderLimitExceeded) return viewerUnsupported('Encoded WebM exceeded the governed output buffer limit', 'export-webm-v1');
            if (signal.aborted) throw new DOMException('Movie export cancelled', 'AbortError');
            const applied = await stepper.apply(frame, signal);
            if (applied.status !== 'ok') return applied as ViewerResult<never>;
            track.requestFrame();
            completedFrames = index + 1;
            onProgress?.(completedFrames);
            await waitUntil(startedAt + completedFrames * frameInterval, signal);
        }
        await waitUntil(startedAt + (completedFrames + 1) * frameInterval, signal);
        if (recorder.state !== 'inactive') recorder.stop();
        await stopped;
        if (encoderLimitExceeded) return viewerUnsupported('Encoded WebM exceeded the governed output buffer limit', 'export-webm-v1');
        const trajectoryFrames = stepper.frames.filter((frame): frame is Extract<MovieFrame, { kind: 'coordinate_trajectory' }> => frame.kind === 'coordinate_trajectory');
        return viewerOk({
            blob: new Blob(chunks, { type: request.codec }),
            completedFrames,
            semanticWarnings: movieSemanticWarnings(stepper.sourceKind),
            sourceFrameRange: trajectoryFrames.length ? { first: trajectoryFrames[0]!.sourceFrame, last: trajectoryFrames.at(-1)!.sourceFrame } : null,
            width: canvas.width, height: canvas.height, fps: request.fps, bitrate: request.bitrate, codec: request.codec,
        });
    } catch (error) {
        return signal.aborted || error instanceof DOMException && error.name === 'AbortError'
            ? { status: 'cancelled', reason: 'Movie export was cancelled and encoder state was released' }
            : { status: 'error', error: error instanceof Error ? error : new Error(String(error)) };
    } finally {
        const activeRecorder = recorder;
        if (activeRecorder && activeRecorder.state !== 'inactive') {
            activeRecorder.stop();
            try { await stopped; } catch { /* terminal cleanup */ }
        }
        for (const track of stream?.getTracks() ?? []) track.stop();
        chunks.length = 0;
    }
};
