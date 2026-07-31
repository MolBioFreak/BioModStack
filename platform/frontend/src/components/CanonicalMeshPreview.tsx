import { useEffect, useMemo, useRef, useState } from 'react';

interface Mesh {
    vertices: Array<[number, number, number]>;
    faces: Array<[number, number, number]>;
}

interface CanonicalMeshPreviewProps {
    url: string;
    label: string;
    height?: number;
}

const parseCanonicalObj = (text: string): Mesh => {
    const vertices: Array<[number, number, number]> = [];
    const faces: Array<[number, number, number]> = [];
    const lines = text.split(/\r?\n/);
    if (lines[0]?.trim() !== '# bms_shape_canonical_obj_v1') {
        throw new Error('Canonical mesh preview has an unexpected schema.');
    }
    for (const raw of lines.slice(1)) {
        const line = raw.trim();
        if (!line) continue;
        const fields = line.split(/\s+/);
        if (fields[0] === 'v' && fields.length === 4) {
            const values = fields.slice(1).map(Number);
            if (!values.every(Number.isFinite)) throw new Error('Canonical mesh preview contains a non-finite vertex.');
            vertices.push([values[0], values[1], values[2]]);
        } else if (fields[0] === 'f' && fields.length === 4) {
            const values = fields.slice(1).map((value) => Number(value) - 1);
            if (!values.every((value) => Number.isSafeInteger(value) && value >= 0)) {
                throw new Error('Canonical mesh preview contains an invalid face.');
            }
            faces.push([values[0], values[1], values[2]]);
        } else {
            throw new Error('Canonical mesh preview contains an unsupported record.');
        }
    }
    if (!vertices.length || !faces.length || faces.some((face) => face.some((index) => index >= vertices.length))) {
        throw new Error('Canonical mesh preview is incomplete.');
    }
    return { vertices, faces };
};

export default function CanonicalMeshPreview({ url, label, height = 430 }: CanonicalMeshPreviewProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [mesh, setMesh] = useState<Mesh | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [yaw, setYaw] = useState(-35);
    const [pitch, setPitch] = useState(22);

    useEffect(() => {
        const controller = new AbortController();
        setMesh(null);
        setError(null);
        fetch(url, { signal: controller.signal })
            .then((response) => {
                if (!response.ok) throw new Error(`Canonical mesh preview request failed (${response.status}).`);
                return response.text();
            })
            .then((text) => setMesh(parseCanonicalObj(text)))
            .catch((cause: unknown) => {
                if (cause instanceof DOMException && cause.name === 'AbortError') return;
                setError(cause instanceof Error ? cause.message : 'Canonical mesh preview failed.');
            });
        return () => controller.abort();
    }, [url]);

    const facts = useMemo(() => mesh ? `${mesh.vertices.length.toLocaleString()} vertices · ${mesh.faces.length.toLocaleString()} faces` : null, [mesh]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const cssWidth = Math.max(320, Math.floor(canvas.getBoundingClientRect().width || 900));
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.floor(cssWidth * ratio);
        canvas.height = Math.floor(height * ratio);
        const context = canvas.getContext('2d');
        if (!context) return;
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        context.clearRect(0, 0, cssWidth, height);
        context.fillStyle = '#020617';
        context.fillRect(0, 0, cssWidth, height);
        if (!mesh) return;

        const yawRadians = yaw * Math.PI / 180;
        const pitchRadians = pitch * Math.PI / 180;
        const cy = Math.cos(yawRadians); const sy = Math.sin(yawRadians);
        const cp = Math.cos(pitchRadians); const sp = Math.sin(pitchRadians);
        const rotated = mesh.vertices.map(([x, y, z]) => {
            const x1 = x * cy + z * sy;
            const z1 = -x * sy + z * cy;
            return [x1, y * cp - z1 * sp, y * sp + z1 * cp] as const;
        });
        const xs = rotated.map((vertex) => vertex[0]);
        const ys = rotated.map((vertex) => vertex[1]);
        const minX = Math.min(...xs); const maxX = Math.max(...xs);
        const minY = Math.min(...ys); const maxY = Math.max(...ys);
        const scale = 0.82 * Math.min(cssWidth / Math.max(maxX - minX, 1e-12), height / Math.max(maxY - minY, 1e-12));
        const centerX = (minX + maxX) / 2;
        const centerY = (minY + maxY) / 2;
        const projected = rotated.map(([x, y, z]) => [
            cssWidth / 2 + (x - centerX) * scale,
            height / 2 - (y - centerY) * scale,
            z,
        ] as const);
        const orderedFaces = mesh.faces
            .map((face) => ({ face, depth: (projected[face[0]][2] + projected[face[1]][2] + projected[face[2]][2]) / 3 }))
            .sort((left, right) => left.depth - right.depth);
        for (const { face } of orderedFaces) {
            const a = projected[face[0]]; const b = projected[face[1]]; const c = projected[face[2]];
            const signedArea = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
            const light = Math.max(0.2, Math.min(0.9, 0.5 + Math.sign(signedArea) * 0.22));
            context.beginPath();
            context.moveTo(a[0], a[1]); context.lineTo(b[0], b[1]); context.lineTo(c[0], c[1]); context.closePath();
            context.fillStyle = `rgba(34, 211, 238, ${light})`;
            context.fill();
            context.strokeStyle = 'rgba(165, 243, 252, 0.18)';
            context.lineWidth = 0.45;
            context.stroke();
        }
    }, [height, mesh, pitch, yaw]);

    return (
        <div className="bg-slate-950" aria-label={label}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-3 py-2 text-xs text-slate-300">
                <span>{facts ?? (error ? 'Preview unavailable' : 'Loading canonical surface…')}</span>
                <div className="flex items-center gap-2">
                    <button type="button" onClick={() => setYaw((value) => value - 15)} className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" aria-label="Rotate canonical mesh left">↶</button>
                    <button type="button" onClick={() => setYaw((value) => value + 15)} className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" aria-label="Rotate canonical mesh right">↷</button>
                    <button type="button" onClick={() => { setYaw(-35); setPitch(22); }} className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800">Reset view</button>
                    <a href={url} download className="rounded border border-cyan-500/40 px-2 py-1 text-cyan-200 hover:bg-cyan-500/10">Download canonical OBJ</a>
                </div>
            </div>
            {error ? <div className="flex items-center justify-center text-sm text-red-300" style={{ height }}>{error}</div> : <canvas ref={canvasRef} className="block w-full" style={{ height }} />}
        </div>
    );
}
