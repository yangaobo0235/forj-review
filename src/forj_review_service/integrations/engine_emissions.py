"""Resolve each complete engine model through the single-input business API."""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, StrictInt

from forj_review_service.contracts.engine_emissions import (
    EngineEmissionLookupUnavailable,
    engine_model_key,
)
from forj_review_service.integrations.vehicle_api import VehicleApi, VehicleApiUnavailable


class EmissionValue(BaseModel):
    # Required key: only an explicit null denotes a normal miss.
    emissionStandard: Literal["国三", "国四", "国五", "国六"] | None


class EmissionResponse(BaseModel):
    code: StrictInt
    data: EmissionValue | None = None


async def lookup_engine_emissions_async(
    models: Iterable[str], *, api: VehicleApi | None = None
) -> dict[str, str]:
    client = api if api is not None else VehicleApi()
    standards = {}
    try:
        for model in sorted({engine_model_key(value) for value in models} - {""}):
            result = await client.post(
                "/vehicle-model/engine-emission/query", {"engineModel": model}, EmissionResponse
            )
            if result.code != 0 or result.data is None:
                raise VehicleApiUnavailable("发动机排放业务查询失败或结果存在冲突，请人工复核")
            if result.data.emissionStandard is not None:
                standards[model] = result.data.emissionStandard
        return standards
    except VehicleApiUnavailable as exc:
        raise EngineEmissionLookupUnavailable(str(exc)) from None
