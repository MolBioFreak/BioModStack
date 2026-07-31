"""Deterministic, bounded Shape Blueprint OBJ admission.

This module intentionally owns only geometry. It has no user, policy, job, or
workflow concepts. Canonical buffers are little-endian and point sampling is
seeded from the canonical geometry identity.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
import math
import struct
import numpy as np
import trimesh


MAX_OBJ_BYTES = 16 * 1024 * 1024
MAX_MESH_BYTES = MAX_OBJ_BYTES
MAX_VERTICES = 20_000
MAX_FACES = 40_000
POINT_COUNT = 4096
SDF_GRID_SIZE = 48
POINT_BATCH = 8192
MAX_POINT_DRAWS = 4_000_000


class ShapeGeometryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CanonicalGeometry:
    source_sha256: str
    geometry_sha256: str
    point_pool_sha256: str
    vertices_f64: bytes
    faces_u32: bytes
    points_f32: bytes
    sdf_f32: bytes
    sdf_sha256: str
    sdf_shape: list[int]
    sdf_origin_angstrom: list[float]
    sdf_spacing_angstrom: list[float]
    preview_obj: bytes
    vertex_count: int
    face_count: int
    point_count: int
    bounds_angstrom: list[float]
    manifest: dict[str, object]


def _fail(code: str, message: str) -> None:
    raise ShapeGeometryError(code, message)


def _parse_obj(payload: bytes) -> tuple[np.ndarray, np.ndarray]:
    if not payload or len(payload) > MAX_OBJ_BYTES:
        _fail("invalid_obj", "OBJ is empty or exceeds the 16 MiB limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ShapeGeometryError("invalid_obj", "OBJ must be UTF-8 text") from exc

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        record = fields[0]
        if record == "v":
            if len(fields) != 4:
                _fail("invalid_obj", f"line {line_number}: vertex must have exactly three coordinates")
            try:
                vertex = tuple(float(value) for value in fields[1:])
            except ValueError as exc:
                raise ShapeGeometryError("invalid_obj", f"line {line_number}: invalid vertex") from exc
            if not all(math.isfinite(value) for value in vertex):
                _fail("non_finite_vertex", f"line {line_number}: vertex is not finite")
            vertices.append(vertex)  # type: ignore[arg-type]
        elif record == "f":
            if len(fields) != 4:
                _fail("non_triangular_face", f"line {line_number}: only triangular faces are accepted")
            face: list[int] = []
            for field in fields[1:]:
                token = field.split("/", 1)[0]
                try:
                    index = int(token)
                except ValueError as exc:
                    raise ShapeGeometryError("invalid_obj", f"line {line_number}: invalid face index") from exc
                if index <= 0:
                    _fail("invalid_obj", f"line {line_number}: relative/zero indices are not accepted")
                face.append(index - 1)
            if len(set(face)) != 3:
                _fail("degenerate_face", f"line {line_number}: repeated face vertex")
            faces.append(tuple(face))  # type: ignore[arg-type]
        else:
            _fail("invalid_obj", f"line {line_number}: unsupported OBJ record {record!r}")
        if len(vertices) > MAX_VERTICES or len(faces) > MAX_FACES:
            _fail("invalid_obj", "OBJ exceeds vertex or face limits")

    if len(vertices) < 4 or len(faces) < 4:
        _fail("open_mesh", "mesh is too small to enclose a volume")
    if any(index >= len(vertices) for face in faces for index in face):
        _fail("invalid_obj", "face references a missing vertex")
    if set(index for face in faces for index in face) != set(range(len(vertices))):
        _fail("invalid_obj", "every admitted vertex must be referenced")
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def _index_triangle_soup(triangles: list[tuple[tuple[float, float, float], ...]]) -> tuple[np.ndarray, np.ndarray]:
    vertices: list[tuple[float, float, float]] = []
    vertex_indices: dict[tuple[float, float, float], int] = {}
    faces: list[tuple[int, int, int]] = []
    for triangle in triangles:
        face: list[int] = []
        for raw_vertex in triangle:
            vertex = tuple(0.0 if value == 0.0 else value for value in raw_vertex)
            typed_vertex = (vertex[0], vertex[1], vertex[2])
            index = vertex_indices.get(typed_vertex)
            if index is None:
                index = len(vertices)
                vertex_indices[typed_vertex] = index
                vertices.append(typed_vertex)
                if len(vertices) > MAX_VERTICES:
                    _fail("invalid_stl", "STL exceeds the vertex limit")
            face.append(index)
        if len(set(face)) != 3:
            _fail("degenerate_face", "STL triangle contains repeated vertices")
        faces.append(tuple(face))  # type: ignore[arg-type]
    if len(vertices) < 4 or len(faces) < 4:
        _fail("open_mesh", "mesh is too small to enclose a volume")
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def _parse_binary_stl(payload: bytes, triangle_count: int) -> tuple[np.ndarray, np.ndarray]:
    if triangle_count > MAX_FACES:
        _fail("invalid_stl", "STL exceeds the face limit")
    expected_size = 84 + triangle_count * 50
    if len(payload) != expected_size:
        _fail("invalid_stl", "binary STL length does not match its triangle count")
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    for index in range(triangle_count):
        values = struct.unpack_from("<12fH", payload, 84 + index * 50)
        if not all(math.isfinite(value) for value in values[:12]):
            _fail("non_finite_vertex", f"binary STL facet {index + 1} contains a non-finite float")
        if values[12] != 0:
            _fail("unsupported_stl_attribute", "binary STL facet attributes are not supported")
        triangles.append((values[3:6], values[6:9], values[9:12]))  # type: ignore[arg-type]
    return _index_triangle_soup(triangles)


def _parse_ascii_stl(payload: bytes) -> tuple[np.ndarray, np.ndarray]:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ShapeGeometryError("invalid_stl", "ASCII STL must contain ASCII text") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or lines[0].split(maxsplit=1)[0].lower() != "solid":
        _fail("invalid_stl", "ASCII STL must start with solid")
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    cursor = 1
    while cursor < len(lines) and lines[cursor].split(maxsplit=1)[0].lower() == "facet":
        fields = lines[cursor].split()
        if len(fields) != 5 or fields[0].lower() != "facet" or fields[1].lower() != "normal":
            _fail("invalid_stl", f"line {cursor + 1}: expected facet normal")
        try:
            normal = tuple(float(value) for value in fields[2:])
        except ValueError as exc:
            raise ShapeGeometryError("invalid_stl", f"line {cursor + 1}: invalid facet normal") from exc
        if not all(math.isfinite(value) for value in normal):
            _fail("invalid_stl", f"line {cursor + 1}: facet normal is not finite")
        cursor += 1
        if cursor >= len(lines) or lines[cursor].lower() != "outer loop":
            _fail("invalid_stl", f"line {cursor + 1}: expected outer loop")
        cursor += 1
        triangle: list[tuple[float, float, float]] = []
        for _ in range(3):
            if cursor >= len(lines):
                _fail("invalid_stl", "ASCII STL ended inside a facet")
            fields = lines[cursor].split()
            if len(fields) != 4 or fields[0].lower() != "vertex":
                _fail("invalid_stl", f"line {cursor + 1}: expected vertex with three coordinates")
            try:
                vertex = tuple(float(value) for value in fields[1:])
            except ValueError as exc:
                raise ShapeGeometryError("invalid_stl", f"line {cursor + 1}: invalid vertex") from exc
            if not all(math.isfinite(value) for value in vertex):
                _fail("non_finite_vertex", f"line {cursor + 1}: vertex is not finite")
            triangle.append(vertex)  # type: ignore[arg-type]
            cursor += 1
        if cursor >= len(lines) or lines[cursor].lower() != "endloop":
            _fail("invalid_stl", f"line {cursor + 1}: expected endloop")
        cursor += 1
        if cursor >= len(lines) or lines[cursor].lower() != "endfacet":
            _fail("invalid_stl", f"line {cursor + 1}: expected endfacet")
        cursor += 1
        triangles.append(tuple(triangle))
        if len(triangles) > MAX_FACES:
            _fail("invalid_stl", "STL exceeds the face limit")
    if cursor >= len(lines) or lines[cursor].split(maxsplit=1)[0].lower() != "endsolid":
        _fail("invalid_stl", f"line {cursor + 1}: expected endsolid")
    if cursor != len(lines) - 1:
        _fail("invalid_stl", "ASCII STL contains trailing records")
    return _index_triangle_soup(triangles)


def _parse_stl(payload: bytes) -> tuple[np.ndarray, np.ndarray, str]:
    if not payload or len(payload) > MAX_MESH_BYTES:
        _fail("invalid_stl", "STL is empty or exceeds the 16 MiB limit")
    if len(payload) >= 84:
        triangle_count = struct.unpack_from("<I", payload, 80)[0]
        if len(payload) == 84 + triangle_count * 50:
            vertices, faces = _parse_binary_stl(payload, triangle_count)
            return vertices, faces, "stl_binary_v1"
    vertices, faces = _parse_ascii_stl(payload)
    return vertices, faces, "stl_ascii_v1"


def _validate_topology(vertices: np.ndarray, faces: np.ndarray) -> None:
    triangles = vertices[faces]
    doubled_areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    )
    if np.any(doubled_areas <= 1e-12):
        _fail("degenerate_face", "mesh contains a zero-area triangle")

    undirected: Counter[tuple[int, int]] = Counter()
    directed: Counter[tuple[int, int]] = Counter()
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for face_index, (a, b, c) in enumerate(faces.tolist()):
        for start, end in ((a, b), (b, c), (c, a)):
            key = (min(start, end), max(start, end))
            undirected[key] += 1
            directed[(start, end)] += 1
            edge_faces[key].append(face_index)
    if any(count != 2 for count in undirected.values()):
        _fail("open_mesh", "every mesh edge must have exactly two incident faces")
    if any(directed[(a, b)] != 1 or directed[(b, a)] != 1 for a, b in undirected):
        _fail("inconsistent_winding", "incident faces must use opposite directed edges")

    adjacency: dict[int, set[int]] = defaultdict(set)
    for owners in edge_faces.values():
        first, second = owners
        adjacency[first].add(second)
        adjacency[second].add(first)
    visited = {0}
    queue = deque([0])
    while queue:
        current = queue.popleft()
        for neighbor in sorted(adjacency[current]):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    if len(visited) != len(faces):
        _fail("disconnected_mesh", "mesh must contain exactly one connected body")
    _reject_self_intersections(vertices, faces)


def _segment_crosses_triangle(start: np.ndarray, end: np.ndarray, triangle: np.ndarray) -> bool:
    direction = end - start
    edge1 = triangle[1] - triangle[0]
    edge2 = triangle[2] - triangle[0]
    h = np.cross(direction, edge2)
    determinant = float(np.dot(edge1, h))
    if abs(determinant) <= 1e-12:
        return False
    inverse = 1.0 / determinant
    offset = start - triangle[0]
    u = inverse * float(np.dot(offset, h))
    if u < -1e-10 or u > 1.0 + 1e-10:
        return False
    q = np.cross(offset, edge1)
    v = inverse * float(np.dot(direction, q))
    if v < -1e-10 or u + v > 1.0 + 1e-10:
        return False
    distance = inverse * float(np.dot(edge2, q))
    return 1e-10 < distance < 1.0 - 1e-10


def _triangles_cross(first: np.ndarray, second: np.ndarray) -> bool:
    for triangle, target in ((first, second), (second, first)):
        for index in range(3):
            if _segment_crosses_triangle(triangle[index], triangle[(index + 1) % 3], target):
                return True
    return False


def _reject_self_intersections(vertices: np.ndarray, faces: np.ndarray) -> None:
    triangles = vertices[faces]
    lower = triangles.min(axis=1)
    upper = triangles.max(axis=1)
    order = np.argsort(lower[:, 0], kind="stable")
    active: list[int] = []
    comparisons = 0
    for raw_index in order:
        index = int(raw_index)
        active = [other for other in active if upper[other, 0] >= lower[index, 0] - 1e-12]
        face_vertices = set(faces[index].tolist())
        for other in active:
            if face_vertices.intersection(faces[other].tolist()):
                continue
            if (
                upper[other, 1] < lower[index, 1] - 1e-12
                or upper[index, 1] < lower[other, 1] - 1e-12
                or upper[other, 2] < lower[index, 2] - 1e-12
                or upper[index, 2] < lower[other, 2] - 1e-12
            ):
                continue
            comparisons += 1
            if comparisons > 2_000_000:
                _fail("mesh_too_complex", "self-intersection broad phase exceeded its bounded work limit")
            if _triangles_cross(triangles[index], triangles[other]):
                _fail("self_intersection", "mesh contains intersecting non-adjacent triangles")
        active.append(index)


def _positive_oriented(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, float]:
    triangles = vertices[faces]
    signed_six_volume = np.einsum(
        "ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])
    )
    volume = float(signed_six_volume.sum() / 6.0)
    if not math.isfinite(volume) or abs(volume) <= 1e-12:
        _fail("zero_volume", "mesh does not enclose a nonzero volume")
    if volume < 0:
        faces = faces[:, [0, 2, 1]]
        volume = -volume
    return faces, volume


def _volume_centroid(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    triangles = vertices[faces]
    six_volumes = np.einsum(
        "ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])
    )
    total = float(six_volumes.sum())
    if total <= 0:
        _fail("zero_volume", "mesh orientation did not produce positive volume")
    return np.sum((triangles[:, 0] + triangles[:, 1] + triangles[:, 2]) * six_volumes[:, None], axis=0) / (4.0 * total)


def _canonical_order(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.lexsort((vertices[:, 2], vertices[:, 1], vertices[:, 0]))
    inverse = np.empty_like(order)
    inverse[order] = np.arange(len(order))
    remapped = inverse[faces]
    rotated: list[tuple[int, int, int]] = []
    for face in remapped.tolist():
        variants = [tuple(face[index:] + face[:index]) for index in range(3)]
        rotated.append(min(variants))
    rotated.sort()
    return vertices[order], np.asarray(rotated, dtype=np.uint32)


def _inside(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    # Odd/even ray casting with a non-axis-aligned fixed direction. Randomly
    # generated candidates almost surely avoid exact edge/vertex intersections.
    direction = np.asarray([1.0, 0.3713906763541037, 0.6948475395770321], dtype=np.float64)
    direction /= np.linalg.norm(direction)
    edge1 = triangles[:, 1] - triangles[:, 0]
    edge2 = triangles[:, 2] - triangles[:, 0]
    h = np.cross(np.broadcast_to(direction, edge2.shape), edge2)
    a = np.einsum("ij,ij->i", edge1, h)
    valid_triangle = np.abs(a) > 1e-12
    result = np.zeros(len(points), dtype=bool)
    for start in range(0, len(points), 128):
        chunk = points[start : start + 128]
        s = chunk[:, None, :] - triangles[None, :, 0, :]
        f = np.zeros_like(a)
        f[valid_triangle] = 1.0 / a[valid_triangle]
        u = np.einsum("ptj,tj->pt", s, h) * f[None, :]
        q = np.cross(s, edge1[None, :, :])
        v = np.einsum("j,ptj->pt", direction, q) * f[None, :]
        distance = np.einsum("tj,ptj->pt", edge2, q) * f[None, :]
        hits = valid_triangle[None, :] & (u >= 0.0) & (v >= 0.0) & ((u + v) <= 1.0) & (distance > 1e-10)
        result[start : start + len(chunk)] = (hits.sum(axis=1) % 2) == 1
    return result


def _point_pool(vertices: np.ndarray, faces: np.ndarray, geometry_sha256: str) -> np.ndarray:
    seed_digest = hashlib.sha256(("bms-shape-points-v1:" + geometry_sha256).encode("ascii")).digest()
    rng = np.random.Generator(np.random.PCG64(int.from_bytes(seed_digest, "big")))
    lower = vertices.min(axis=0)
    upper = vertices.max(axis=0)
    triangles = vertices[faces]
    accepted: list[np.ndarray] = []
    accepted_count = 0
    draws = 0
    while accepted_count < POINT_COUNT and draws < MAX_POINT_DRAWS:
        count = min(POINT_BATCH, MAX_POINT_DRAWS - draws)
        candidates = rng.random((count, 3), dtype=np.float64) * (upper - lower) + lower
        inside = candidates[_inside(candidates, triangles)]
        if len(inside):
            take = inside[: POINT_COUNT - accepted_count]
            accepted.append(take)
            accepted_count += len(take)
        draws += count
    if accepted_count != POINT_COUNT:
        _fail("point_sampling_failed", "could not fill the deterministic interior point pool")
    points = np.concatenate(accepted, axis=0).astype("<f4")
    points[points == 0] = 0.0
    return points


def _signed_distance_grid(
    vertices: np.ndarray,
    faces: np.ndarray,
) -> tuple[np.ndarray, list[float], list[float]]:
    lower = vertices.min(axis=0)
    upper = vertices.max(axis=0)
    extent = upper - lower
    padding = max(4.0, float(extent.max()) * 0.1)
    origin = lower - padding
    limit = upper + padding
    axes = [np.linspace(origin[i], limit[i], SDF_GRID_SIZE, dtype=np.float64) for i in range(3)]
    spacing = [(float(limit[i] - origin[i]) / (SDF_GRID_SIZE - 1)) for i in range(3)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape((-1, 3))
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False, validate=False)
    values = np.empty(len(grid), dtype=np.float64)
    for start in range(0, len(grid), 16_384):
        values[start : start + 16_384] = trimesh.proximity.signed_distance(
            mesh, grid[start : start + 16_384]
        )
    if not np.all(np.isfinite(values)):
        _fail("sdf_generation_failed", "canonical signed-distance grid is non-finite")
    sdf = values.reshape((SDF_GRID_SIZE, SDF_GRID_SIZE, SDF_GRID_SIZE)).astype("<f4")
    sdf[sdf == 0] = 0.0
    return sdf, [float(value) for value in origin], spacing


def _preview_obj(vertices: np.ndarray, faces: np.ndarray) -> bytes:
    lines = ["# bms_shape_canonical_obj_v1"]
    lines.extend(f"v {x:.17g} {y:.17g} {z:.17g}" for x, y, z in vertices)
    lines.extend(f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces.tolist())
    return ("\n".join(lines) + "\n").encode("ascii")


def _canonicalize_parsed(
    payload: bytes,
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    angstrom_per_unit: float,
    source_manifest: dict[str, object] | None = None,
) -> CanonicalGeometry:
    source_sha256 = hashlib.sha256(payload).hexdigest()
    vertices = vertices * float(angstrom_per_unit)
    _validate_topology(vertices, faces)
    faces, _ = _positive_oriented(vertices, faces)
    vertices = vertices - _volume_centroid(vertices, faces)
    vertices, faces_u32 = _canonical_order(vertices, faces)
    vertices = np.asarray(vertices, dtype="<f8")
    faces_u32 = np.asarray(faces_u32, dtype="<u4")
    vertices[vertices == 0] = 0.0
    vertex_bytes = vertices.tobytes(order="C")
    face_bytes = faces_u32.tobytes(order="C")
    manifest = {
        "schema": "bms_shape_canonical_geometry_v1",
        "source_sha256": source_sha256,
        "angstrom_per_unit": float(angstrom_per_unit),
        "vertex_count": int(len(vertices)),
        "face_count": int(len(faces_u32)),
        "vertices_sha256": hashlib.sha256(vertex_bytes).hexdigest(),
        "faces_sha256": hashlib.sha256(face_bytes).hexdigest(),
        "bounds_angstrom": [float(value) for value in np.concatenate((vertices.min(axis=0), vertices.max(axis=0)))],
        **(source_manifest or {}),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    geometry_sha256 = hashlib.sha256(manifest_bytes + vertex_bytes + face_bytes).hexdigest()
    points = _point_pool(vertices, faces_u32.astype(np.int64), geometry_sha256)
    point_bytes = points.tobytes(order="C")
    point_pool_sha256 = hashlib.sha256(point_bytes).hexdigest()
    sdf, sdf_origin, sdf_spacing = _signed_distance_grid(vertices, faces_u32.astype(np.int64))
    sdf_bytes = sdf.tobytes(order="C")
    sdf_sha256 = hashlib.sha256(sdf_bytes).hexdigest()
    manifest = {
        **manifest,
        "geometry_sha256": geometry_sha256,
        "point_count": POINT_COUNT,
        "point_pool_sha256": point_pool_sha256,
        "sdf_grid_shape": [SDF_GRID_SIZE, SDF_GRID_SIZE, SDF_GRID_SIZE],
        "sdf_origin_angstrom": sdf_origin,
        "sdf_spacing_angstrom": sdf_spacing,
        "sdf_sha256": sdf_sha256,
        "sdf_sign": "positive_inside",
    }
    return CanonicalGeometry(
        source_sha256=source_sha256,
        geometry_sha256=geometry_sha256,
        point_pool_sha256=point_pool_sha256,
        vertices_f64=vertex_bytes,
        faces_u32=face_bytes,
        points_f32=point_bytes,
        sdf_f32=sdf_bytes,
        sdf_sha256=sdf_sha256,
        sdf_shape=[SDF_GRID_SIZE, SDF_GRID_SIZE, SDF_GRID_SIZE],
        sdf_origin_angstrom=sdf_origin,
        sdf_spacing_angstrom=sdf_spacing,
        preview_obj=_preview_obj(vertices, faces_u32),
        vertex_count=len(vertices),
        face_count=len(faces_u32),
        point_count=POINT_COUNT,
        bounds_angstrom=manifest["bounds_angstrom"],  # type: ignore[arg-type]
        manifest=manifest,
    )


def canonicalize_obj(payload: bytes, *, angstrom_per_unit: float) -> CanonicalGeometry:
    if not math.isfinite(angstrom_per_unit) or angstrom_per_unit <= 0 or angstrom_per_unit > 1e9:
        _fail("invalid_scale", "angstrom_per_unit must be finite and in (0, 1e9]")
    vertices, faces = _parse_obj(payload)
    return _canonicalize_parsed(
        payload,
        vertices=vertices,
        faces=faces,
        angstrom_per_unit=angstrom_per_unit,
    )


def canonicalize_mesh(
    payload: bytes,
    *,
    source_format: str,
    angstrom_per_unit: float,
) -> CanonicalGeometry:
    normalized_format = source_format.strip().lower()
    if normalized_format == "obj":
        return canonicalize_obj(payload, angstrom_per_unit=angstrom_per_unit)
    if normalized_format != "stl":
        _fail("unsupported_format", "supported mesh formats are OBJ and STL")
    if not math.isfinite(angstrom_per_unit) or angstrom_per_unit <= 0 or angstrom_per_unit > 1e9:
        _fail("invalid_scale", "angstrom_per_unit must be finite and in (0, 1e9]")
    vertices, faces, parser = _parse_stl(payload)
    return _canonicalize_parsed(
        payload,
        vertices=vertices,
        faces=faces,
        angstrom_per_unit=angstrom_per_unit,
        source_manifest={"source_format": "stl", "source_parser": parser},
    )
