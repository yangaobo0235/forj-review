"""Deterministic business fixtures, compiled exactly like relational page definitions.

These four declarations are synthetic test inputs. Application startup never reads tests.
"""

import json
from copy import deepcopy
from pathlib import Path

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.page_config.loader import load_definitions
from forj_review_service.businesses.profiles import CONSISTENCY_DEFAULT
from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.contracts.review import BusinessType

_DEFINITIONS = tuple(
    json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((Path(__file__).parents[1] / "fixtures/pages").glob("*.json"))
)


_PAGES = load_definitions(_DEFINITIONS, {d["page_id"]: d["version"] for d in _DEFINITIONS})
BUSINESS_PROFILES = (*_PAGES.profiles, CONSISTENCY_DEFAULT)
SCRAP_REPLACEMENT_QINGDAO = next(
    p for p in _PAGES.profiles if p.page_id == "scrap_replacement:qingdao"
)
SCRAP_REPLACEMENT_CHANGCHUN = next(
    p for p in _PAGES.profiles if p.page_id == "scrap_replacement:changchun"
)
VEHICLE_SOURCE_DEFAULT = next(p for p in _PAGES.profiles if p.business_type == "vehicle_source")
TRANSFER_DEFAULT = next(p for p in _PAGES.profiles if p.business_type == "transfer")
SCRAP_REPLACEMENT_PACK = SCRAP_REPLACEMENT_QINGDAO.pack_declaration
VEHICLE_SOURCE_PACK = VEHICLE_SOURCE_DEFAULT.pack_declaration
TRANSFER_PACK = TRANSFER_DEFAULT.pack_declaration
BUSINESS_PACKS = {
    BusinessType(p.business_type): p.pack_declaration
    for p in (SCRAP_REPLACEMENT_QINGDAO, VEHICLE_SOURCE_DEFAULT, TRANSFER_DEFAULT)
}
SCRAP_BUSINESS = BusinessContext.from_profile(SCRAP_REPLACEMENT_QINGDAO)
VEHICLE_BUSINESS = BusinessContext.from_profile(VEHICLE_SOURCE_DEFAULT)
TRANSFER_BUSINESS = BusinessContext.from_profile(TRANSFER_DEFAULT)


def business_registry():
    return BusinessRegistry(BUSINESS_PROFILES)


def build_scrap_profile(region):
    return next(
        p for p in _PAGES.profiles if p.business_type == "scrap_replacement" and p.region == region
    )


def page_definition(profile):
    definition = next(
        (
            d
            for d in _DEFINITIONS
            if d["page_id"] == profile.page_id and d["version"] == profile.version
        ),
        None,
    )
    if definition is None:
        raise ValueError("Unknown test page")
    return {**deepcopy(definition), "active": True}
