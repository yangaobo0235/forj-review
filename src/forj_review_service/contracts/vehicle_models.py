"""车型查询的独立协议，不承载数据库连接参数。"""

import re
import unicodedata

from pydantic import BaseModel, Field, field_validator


def model_code(value: object) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).upper()


def engine_code(value: object) -> str:
    return re.sub(r"^[\u3400-\u9fff]+", "", model_code(value))


class VehicleModelPageScope(BaseModel):
    brandName: str = Field(min_length=1, max_length=120)
    seriesName: str = Field(min_length=1, max_length=120)
    typeLevel: str = Field(min_length=1, max_length=40)

    @field_validator("brandName", "seriesName", "typeLevel", mode="before")
    @classmethod
    def trim_scope(cls, value):
        return str(value or "").strip()


class VehicleModelSearch(BaseModel):
    announcementModel: str = Field(min_length=2, max_length=80)
    engineModel: str = Field(min_length=2, max_length=80)
    pageScope: VehicleModelPageScope

    @field_validator("announcementModel", "engineModel", mode="before")
    @classmethod
    def validate_code(cls, value, info):
        code = engine_code(value) if info.field_name == "engineModel" else model_code(value)
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{1,79}", code):
            raise ValueError("请输入完整型号，保留型号后缀")
        return code


class VehicleModelCandidate(BaseModel):
    modelId: str
    modelName: str
    brandId: str
    brandName: str
    seriesId: str
    seriesName: str
    typeLevel: str
    sameNameCount: int = 1
    config: dict[str, object | None]
    warnings: list[str] = Field(default_factory=list)
