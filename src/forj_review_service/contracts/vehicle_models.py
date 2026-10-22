"""Page-bound, unpaginated model search and six specification values."""

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


def model_code(value: object) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).upper()


def engine_code(value: object) -> str:
    return re.sub(r"^[\u3400-\u9fff]+", "", model_code(value))


class VehicleModelPageScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brandId: str = Field(pattern=r"^[0-9]{1,19}$")
    brandName: str = Field(default="", max_length=120)
    seriesId: str = Field(pattern=r"^[0-9]{1,19}$")
    seriesName: str = Field(default="", max_length=120)
    typeLevel: str = Field(min_length=1, max_length=40)

    @field_validator("brandId", "seriesId")
    @classmethod
    def valid_id(cls, value):
        if not 0 < int(value) <= 9223372036854775807:
            raise ValueError("业务 ID 超出有效范围")
        return value

    @field_validator("brandName", "seriesName", "typeLevel", mode="before")
    @classmethod
    def trim_scope(cls, value):
        return str(value or "").strip()


class VehicleModelSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    announcementModel: str = Field(default="", max_length=80)
    engineModel: str = Field(default="", max_length=80)
    pageScope: VehicleModelPageScope

    @field_validator("announcementModel", "engineModel", mode="before")
    @classmethod
    def validate_code(cls, value, info):
        code = engine_code(value) if info.field_name == "engineModel" else model_code(value)
        if code and not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{1,79}", code):
            raise ValueError("请输入完整型号，保留型号后缀")
        return code


class VehicleModelConfig(BaseModel):
    driveForm: str | None
    fuelType: str | None
    emissionStandard: str | None
    maxHp: str | None
    engineBrand: str | None
    engineType: str | None


class VehicleModelCandidate(BaseModel):
    modelId: str = Field(pattern=r"^[0-9]{1,19}$")
    modelName: str = Field(min_length=1, max_length=500)
    config: VehicleModelConfig

    @field_validator("modelId")
    @classmethod
    def valid_id(cls, value):
        return VehicleModelPageScope.valid_id(value)


class VehicleModelSearchResult(BaseModel):
    candidates: list[VehicleModelCandidate]
