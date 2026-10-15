"""业务配置解析。

主要职责：安全解析精确配置及允许的默认地区回退。
修改日期：2026-08-26
修改人：wuyi
"""

from collections import defaultdict
from types import MappingProxyType

from app.businesses.profiles import BUSINESS_PROFILES, BusinessProfile
from app.models.review import BusinessType, Region


class BusinessProfileNotFound(LookupError):
    """Raised when the requested business profile cannot be resolved safely."""


class BusinessRegistry:
    def __init__(self, profiles: tuple[BusinessProfile, ...]) -> None:
        grouped: dict[tuple[BusinessType, Region, str], list[BusinessProfile]] = defaultdict(list)
        for profile in profiles:
            grouped[(profile.business_type, profile.region, profile.version)].append(profile)
        self._profiles = MappingProxyType({key: tuple(items) for key, items in grouped.items()})

    @property
    def profiles(self) -> tuple[BusinessProfile, ...]:
        return tuple(profile for profiles in self._profiles.values() for profile in profiles)

    def resolve(
        self,
        business_type: BusinessType,
        region: Region,
        version: str,
        page_id: str | None = None,
    ) -> BusinessProfile:
        """解析请求对应的业务配置，仅执行显式允许的地区回退。"""

        exact = self._profiles.get((business_type, region, version), ())
        resolved = self._select(exact, page_id)
        if resolved is not None:
            return resolved
        default = self._profiles.get((business_type, Region.DEFAULT, version), ())
        resolved = self._select(default, page_id)
        if resolved is not None:
            return resolved
        raise BusinessProfileNotFound(
            f"No profile for {business_type.value}/{region.value}/{version}"
        )

    @staticmethod
    def _select(
        candidates: tuple[BusinessProfile, ...], page_id: str | None
    ) -> BusinessProfile | None:
        if not candidates:
            return None
        if page_id:
            for candidate in candidates:
                if candidate.page_id == page_id:
                    return candidate
            return None
        if len(candidates) > 1:
            raise BusinessProfileNotFound(
                "Multiple pages share this business, region and profile version; page_id is required"
            )
        return candidates[0]


def build_business_registry() -> BusinessRegistry:
    return BusinessRegistry(BUSINESS_PROFILES)
