"""Application owner registers this optional stateless preparation router."""
from fastapi import APIRouter, HTTPException
from services.molecular_geometry import GeometryUnavailable, construct_geometry
from services.molecular_geometry_recipe import MolecularGeometryRequest, MolecularGeometryResult

router = APIRouter(prefix='/api/molecular-geometry', tags=['molecular-geometry'])


@router.get('/schema')
def molecular_geometry_schema():
    """Discovery only; does not load or execute the optional native runtime."""
    return {
        'request': MolecularGeometryRequest.model_json_schema(),
        'result': MolecularGeometryResult.model_json_schema(),
    }


@router.post('/construct', response_model=MolecularGeometryResult)
def molecular_geometry(request: MolecularGeometryRequest):
    try:
        return construct_geometry(request)
    except GeometryUnavailable as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502,detail=str(exc)) from exc
