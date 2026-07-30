"""Small deterministic primitive library for Shape Blueprint V1."""

from __future__ import annotations

import math

from services.shape_geometry import ShapeGeometryError


_MIN_ANGSTROM = 4.0
_MAX_ANGSTROM = 1000.0


def _dimension(parameters: dict[str, float], name: str) -> float:
    try:
        value = float(parameters[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ShapeGeometryError("invalid_preset", f"preset requires numeric {name!r}") from exc
    if not math.isfinite(value) or not _MIN_ANGSTROM <= value <= _MAX_ANGSTROM:
        raise ShapeGeometryError(
            "invalid_preset",
            f"preset dimension {name!r} must be between {_MIN_ANGSTROM:g} and {_MAX_ANGSTROM:g} angstrom",
        )
    return value


def _serialize(vertices: list[tuple[float, float, float]], faces: list[tuple[int, int, int]]) -> bytes:
    lines = ["# bms_shape_preset_obj_v1"]
    lines.extend(f"v {x:.17g} {y:.17g} {z:.17g}" for x, y, z in vertices)
    lines.extend(f"f {a} {b} {c}" for a, b, c in faces)
    return ("\n".join(lines) + "\n").encode("ascii")


def _box(parameters: dict[str, float]) -> bytes:
    x, y, z = (_dimension(parameters, name) / 2.0 for name in ("x", "y", "z"))
    vertices = [
        (-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
        (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z),
    ]
    faces = [
        (1, 3, 2), (1, 4, 3), (5, 6, 7), (5, 7, 8),
        (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6),
        (3, 4, 8), (3, 8, 7), (4, 1, 5), (4, 5, 8),
    ]
    return _serialize(vertices, faces)


def _ellipsoid(parameters: dict[str, float]) -> bytes:
    x, y, z = (_dimension(parameters, name) / 2.0 for name in ("x", "y", "z"))
    vertices = [(x, 0, 0), (-x, 0, 0), (0, y, 0), (0, -y, 0), (0, 0, z), (0, 0, -z)]
    faces = [
        (5, 1, 3), (5, 3, 2), (5, 2, 4), (5, 4, 1),
        (6, 3, 1), (6, 2, 3), (6, 4, 2), (6, 1, 4),
    ]
    return _serialize(vertices, faces)


def _cylinder(parameters: dict[str, float]) -> bytes:
    diameter = _dimension(parameters, "diameter")
    height = _dimension(parameters, "height")
    radius = diameter / 2.0
    half = height / 2.0
    sides = 32
    vertices: list[tuple[float, float, float]] = [(0.0, 0.0, -half), (0.0, 0.0, half)]
    vertices.extend((radius * math.cos(2 * math.pi * i / sides), radius * math.sin(2 * math.pi * i / sides), -half) for i in range(sides))
    vertices.extend((radius * math.cos(2 * math.pi * i / sides), radius * math.sin(2 * math.pi * i / sides), half) for i in range(sides))
    faces: list[tuple[int, int, int]] = []
    for i in range(sides):
        nxt = (i + 1) % sides
        bottom, bottom_next = 3 + i, 3 + nxt
        top, top_next = 3 + sides + i, 3 + sides + nxt
        faces.extend(((1, bottom_next, bottom), (2, top, top_next), (bottom, bottom_next, top_next), (bottom, top_next, top)))
    return _serialize(vertices, faces)


def build_preset_obj(kind: str, parameters: dict[str, float]) -> bytes:
    expected = {
        "box": {"x", "y", "z"},
        "ellipsoid": {"x", "y", "z"},
        "cylinder": {"diameter", "height"},
    }
    if kind not in expected:
        raise ShapeGeometryError("invalid_preset", "preset must be box, ellipsoid, or cylinder")
    if set(parameters) != expected[kind]:
        raise ShapeGeometryError("invalid_preset", f"{kind} parameters must be exactly {sorted(expected[kind])}")
    return {"box": _box, "ellipsoid": _ellipsoid, "cylinder": _cylinder}[kind](parameters)
