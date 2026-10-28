"""Route-free preparation leaf; the application owner registers this router.

No persistence, CIF adoption, arbitrary provider execution or model submission.
"""
from fastapi import APIRouter, HTTPException
from services.molecular_construction import ConstructionUnavailable, construct_graph, create_complement
from services.molecular_construction_recipe import (
    ComplementRequest, ConstructionResult, MolecularConstructionRecipe, Strand,
)

router = APIRouter(prefix="/api/molecular-construction", tags=["molecular-construction"])


@router.post("/graph", response_model=ConstructionResult)
def molecular_graph(recipe: MolecularConstructionRecipe):
    try:
        return construct_graph(recipe)
    except ConstructionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/complement", response_model=Strand)
def complementary_strand(request: ComplementRequest):
    try:
        return create_complement(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
