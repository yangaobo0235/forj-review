"""Business vehicle API transport; credentials stay on the review backend."""

import os
from dataclasses import dataclass, field
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from forj_review_service.config.settings import load_dotenv

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class VehicleApiUnavailable(Exception):
    """Transport, authentication, business errors and invalid responses fail closed."""


@dataclass(frozen=True)
class VehicleApiSettings:
    # Include the gateway prefix, e.g. https://vehicle.example/api/admin.
    base_url: str = ""
    token: str = field(default="", repr=False)
    timeout: float = 15.0


def load_vehicle_api_settings() -> VehicleApiSettings:
    load_dotenv()
    try:
        timeout = float(os.getenv("REVIEW_VEHICLE_API_TIMEOUT", "15"))
    except ValueError:
        timeout = 0
    return VehicleApiSettings(
        os.getenv("REVIEW_VEHICLE_API_BASE_URL", "").strip().rstrip("/"),
        os.getenv("REVIEW_VEHICLE_API_TOKEN", "").strip(),
        timeout,
    )


class VehicleApi:
    def __init__(
        self,
        settings: VehicleApiSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings if settings is not None else load_vehicle_api_settings()
        self.transport = transport

    async def post(
        self, path: str, payload: dict[str, str], response_type: type[ResponseModel]
    ) -> ResponseModel:
        settings = self.settings
        try:
            url = urlsplit(settings.base_url)
            valid = (
                url.scheme in {"http", "https"}
                and url.netloc
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and 0 < settings.timeout <= 120
            )
        except ValueError:
            valid = False
        if not valid:
            raise VehicleApiUnavailable("车型业务接口未配置或配置无效，请联系管理员")
        headers = {"Authorization": "Bearer " + settings.token} if settings.token else {}
        try:
            # Jobs use separate event loops; each request owns its HTTP resources.
            async with httpx.AsyncClient(
                timeout=settings.timeout,
                headers=headers,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(settings.base_url + path, json=payload)
                response.raise_for_status()
                return response_type.model_validate_json(response.content)
        except (httpx.HTTPError, httpx.InvalidURL, ValidationError, ValueError):
            # Never expose upstream bodies, URLs or credentials to reviewers/logs.
            raise VehicleApiUnavailable("车型业务接口调用失败或响应无效，请稍后重试") from None
