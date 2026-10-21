"""Business-specific test values derived from complete page fixtures, never part of the runtime application."""

from forj_review_service.businesses.rules.transfer_contract import (
    NEW_PREFIX,
    USED_PREFIX,
)
from forj_review_service.contracts.review import Region
from tests.support.pages import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_PACK,
    TRANSFER_PACK,
    VEHICLE_SOURCE_PACK,
)

OLD_SECTION = "old_vehicle"

QINGDAO_REPLACEMENT_POLICY = SCRAP_REPLACEMENT_PACK.region(Region.QINGDAO).replacement_policy
CHANGCHUN_REPLACEMENT_POLICY = SCRAP_REPLACEMENT_CHANGCHUN.replacement_policy

USED_FIELDS = tuple(
    (field.key.split(".", 1)[1], field.label, field.aliases, field.normalizer)
    for field in TRANSFER_PACK.fields
    if field.key.startswith(USED_PREFIX + ".")
)
NEW_FIELDS = tuple(
    (field.key.split(".", 1)[1], field.label, field.aliases, field.normalizer)
    for field in TRANSFER_PACK.fields
    if field.key.startswith(NEW_PREFIX + ".")
)
USED_KEYS = tuple(f"{USED_PREFIX}.{name}" for name, *_ in USED_FIELDS)
NEW_KEYS = tuple(f"{NEW_PREFIX}.{name}" for name, *_ in NEW_FIELDS)

LICENSE_FIELDS = tuple(
    (field.key.split(".", 1)[1], field.label, field.aliases, field.normalizer)
    for field in VEHICLE_SOURCE_PACK.fields
    if field.section == "license" and field.mode == "SINGLE_SOURCE"
)
