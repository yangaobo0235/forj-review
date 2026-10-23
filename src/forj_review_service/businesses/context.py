"""Explicit immutable business policies; no request-local globals or reflection."""

from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forj_review_service.businesses.packs.model import BusinessExtensionPack
    from forj_review_service.businesses.profiles import BusinessProfile


@dataclass(frozen=True)
class BusinessContext:
    pack: "BusinessExtensionPack | None" = None

    @classmethod
    def from_profile(cls, profile: "BusinessProfile") -> "BusinessContext":
        return cls(profile.pack_declaration)

    @cached_property
    def labels(self):
        return MappingProxyType(self.pack.labels() if self.pack else {})

    @cached_property
    def completeness_labels(self):
        from forj_review_service.businesses.completeness import FIELD_LABELS

        return MappingProxyType({**FIELD_LABELS, **self.labels})

    @cached_property
    def field_policies(self):
        from forj_review_service.businesses.field_policies import build_policies

        return MappingProxyType(build_policies(self.pack) if self.pack else {})

    @cached_property
    def material_fields(self):
        return MappingProxyType(
            {f.key: f.material_field for f in self.pack.fields if f.material_field is not None}
            if self.pack
            else {}
        )

    @cached_property
    def document_policies(self):
        from forj_review_service.businesses.materials import merge_material

        return MappingProxyType(
            {d.document_type: merge_material(d, None) for d in self.pack.materials}
            if self.pack
            else {}
        )

    @cached_property
    def composites(self):
        from forj_review_service.evidence.comparison.composite_fields import declared_composites

        return declared_composites((self.pack,)) if self.pack else ()

    @cached_property
    def composites_by_member(self):
        return MappingProxyType(
            {
                field: spec
                for spec in self.composites
                for field in (spec.primary_field, spec.secondary_field)
            }
        )


# An unconfigured context cannot acquire another page's business rules.
DEFAULT_BUSINESS = BusinessContext()
