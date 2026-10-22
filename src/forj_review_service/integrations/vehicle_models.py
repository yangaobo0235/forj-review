"""Search authoritative business candidates without local catalog fallback."""

from pydantic import BaseModel, StrictInt

from forj_review_service.contracts.errors import VehicleModelCatalogUnavailable
from forj_review_service.contracts.vehicle_models import (
    VehicleModelCandidate,
    VehicleModelSearch,
    VehicleModelSearchResult,
)
from forj_review_service.integrations.vehicle_api import VehicleApi, VehicleApiUnavailable


class ModelSearchResponse(BaseModel):
    code: StrictInt
    data: list[VehicleModelCandidate] | None = None


class VehicleModelCatalog:
    def __init__(self, api: VehicleApi | None = None):
        self.api = api if api is not None else VehicleApi()

    async def search(self, query: VehicleModelSearch) -> VehicleModelSearchResult:
        scope = query.pageScope
        try:
            result = await self.api.post("/vehicle-model/review/search", {
                "brandId": scope.brandId, "serialId": scope.seriesId,
                "typeLevel": scope.typeLevel,
                "announcementModel": query.announcementModel, "engineModel": query.engineModel,
            }, ModelSearchResponse)
            if result.code != 0 or result.data is None:
                raise VehicleApiUnavailable("车型业务查询失败，请稍后重试")
            if len({item.modelId for item in result.data}) != len(result.data):
                raise VehicleApiUnavailable("车型业务接口返回重复车型 ID，请联系管理员")
            return VehicleModelSearchResult(candidates=result.data)
        except VehicleApiUnavailable as exc:
            raise VehicleModelCatalogUnavailable(str(exc)) from None
