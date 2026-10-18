"""审核助手自己的车型只读查询接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from forj_review_assistant.contracts.vehicle_models import VehicleModelCandidate, VehicleModelSearch
from forj_review_assistant.integrations.vehicle_models import (
    VehicleModelCatalog,
    VehicleModelCatalogUnavailable,
)

router = APIRouter()


def model_catalog():
    return VehicleModelCatalog()


@router.post("/api/review/vehicle-models/search", response_model=list[VehicleModelCandidate])
def search_vehicle_models(query: VehicleModelSearch, catalog: Annotated[VehicleModelCatalog, Depends(model_catalog)]):
    try:
        return catalog.search(query)
    except VehicleModelCatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
