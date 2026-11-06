#!/usr/bin/env python3
"""One CPU preparation call; stdout is a typed result/error, not native logging."""
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'platform/api'))
# Load only the stateless leaves. The application services package initializer
# eagerly imports Nextflow/workflow adapters and API-only dependencies.
from types import ModuleType
services = ModuleType('services')
services.__path__ = [str(ROOT / 'platform/api/services')]
sys.modules['services'] = services


def main():
    output = sys.stdout
    with redirect_stdout(sys.stderr):
        from services.molecular_construction import construct_graph, ConstructionUnavailable
        from services.molecular_construction_recipe import MolecularConstructionRecipe
        from services.molecular_geometry import construct_geometry, GeometryUnavailable
        from services.molecular_geometry_recipe import MolecularGeometryRequest
        operations = {'graph': (MolecularConstructionRecipe, construct_graph),
                      'geometry': (MolecularGeometryRequest, construct_geometry)}
        schema, native = operations[sys.argv[1]]
        request = schema.model_validate_json(sys.stdin.read())
        try:
            reply = {'result': native(request).model_dump(mode='json')}
        except (ConstructionUnavailable, GeometryUnavailable, ValueError, RuntimeError) as exc:
            # Preserve the existing routes' original exception classes and details.
            kind = next(cls.__name__ for cls in (ConstructionUnavailable, GeometryUnavailable, ValueError, RuntimeError)
                        if isinstance(exc, cls))
            reply = {'error': kind, 'detail': str(exc)}
    output.write(json.dumps(reply))


if __name__ == '__main__':
    main()
