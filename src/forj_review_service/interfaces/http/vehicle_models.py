"""审核助手自己的车型只读查询接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from forj_review_service.contracts.vehicle_models import VehicleModelCandidate, VehicleModelSearch
from forj_review_service.integrations.vehicle_models import (
    VehicleModelCatalog,
)

router = APIRouter()


def model_catalog(request: Request):
    return VehicleModelCatalog(getattr(request.app.state, "database", None))


@router.post("/api/review/vehicle-models/search", response_model=list[VehicleModelCandidate])
async def search_vehicle_models(
    query: VehicleModelSearch, catalog: Annotated[VehicleModelCatalog, Depends(model_catalog)]
):
    return await catalog.search(query)
