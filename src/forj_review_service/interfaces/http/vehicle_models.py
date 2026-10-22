"""Review-facing page-bound model search proxy."""

from typing import Annotated

from fastapi import APIRouter, Depends

from forj_review_service.contracts.vehicle_models import (
    VehicleModelSearch,
    VehicleModelSearchResult,
)
from forj_review_service.integrations.vehicle_models import VehicleModelCatalog

router = APIRouter()


def model_catalog():
    return VehicleModelCatalog()


@router.post("/api/review/vehicle-models/search", response_model=VehicleModelSearchResult)
async def search_vehicle_models(
    query: VehicleModelSearch, catalog: Annotated[VehicleModelCatalog, Depends(model_catalog)]
):
    return await catalog.search(query)
