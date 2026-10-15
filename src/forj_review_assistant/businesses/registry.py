"""业务配置解析。

主要职责：安全解析精确配置及允许的默认地区回退。
修改日期：2026-08-26
修改人：wuyi
"""

from collections import defaultdict
from pathlib import Path
from types import MappingProxyType

from forj_review_assistant.businesses.profiles import BUSINESS_PROFILES, BusinessProfile
from forj_review_assistant.contracts.review import BusinessId, Region, RegionId


class BusinessProfileNotFound(LookupError):
    """Raised when the requested business profile cannot be resolved safely."""


class BusinessRegistry:
    def __init__(
        self,
        profiles: tuple[BusinessProfile, ...],
        *,
        active_profiles: tuple[BusinessProfile, ...] | None = None,
    ) -> None:
        grouped: dict[tuple[BusinessId, RegionId, str], list[BusinessProfile]] = defaultdict(list)
        identities = {}
        owners = {}
        for profile in profiles:
            page_id = profile.page_id or f"{profile.business_type}:{profile.region}"
            identity = (page_id, profile.version)
            if identity in identities:
                raise ValueError(f"页面身份和版本重复：{identity}")
            owner = (profile.business_type, profile.region)
            if page_id in owners and owners[page_id] != owner:
                raise ValueError(f"同一 page_id 不能跨版本更换业务或地区：{page_id}")
            owners[page_id] = owner
            identities[identity] = profile
            grouped[(profile.business_type, profile.region, profile.version)].append(profile)
        self._profiles = MappingProxyType({key: tuple(items) for key, items in grouped.items()})
        if active_profiles is None:
            active = {p.page_id or f"{p.business_type}:{p.region}": p for p in profiles}
            active_profiles = tuple(active.values())
        active_ids = set()
        for profile in active_profiles:
            page_id = profile.page_id or f"{profile.business_type}:{profile.region}"
            if identities.get((page_id, profile.version)) is not profile:
                raise ValueError(f"活动配置必须属于当前注册表快照：{page_id}/{profile.version}")
            if page_id in active_ids:
                raise ValueError(f"同一 page_id 只能有一个活动版本：{page_id}")
            active_ids.add(page_id)
        self._active_profiles = tuple(active_profiles)

    @property
    def active_profiles(self) -> tuple[BusinessProfile, ...]:
        return self._active_profiles

    @property
    def profiles(self) -> tuple[BusinessProfile, ...]:
        return tuple(profile for profiles in self._profiles.values() for profile in profiles)

    def resolve(
        self,
        business_type: BusinessId,
        region: RegionId,
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
        raise BusinessProfileNotFound(f"No profile for {business_type}/{region}/{version}")

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


def build_business_registry(directory: Path | None = None) -> BusinessRegistry:
    import os

    from forj_review_assistant.businesses.configuration.loader import load_page_profiles
    from forj_review_assistant.config.settings import load_dotenv

    load_dotenv()
    configured = os.getenv("REVIEW_PAGES_DIR")
    directory = directory if directory is not None else Path(configured) if configured else None
    if directory is None:
        return BusinessRegistry(BUSINESS_PROFILES)
    pages = load_page_profiles(directory, BUSINESS_PROFILES)
    return BusinessRegistry((*BUSINESS_PROFILES, *pages.profiles), active_profiles=pages.active)
