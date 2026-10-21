"""Mappings of existing tables. Schema changes belong exclusively to migrations.

The four imported vehicle mappings expose only columns used by this application;
they never create, alter or write the upstream business tables.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, FetchedValue, Identity, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        str: Text,
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        datetime: DateTime(timezone=True),
    }


def identity():
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def timestamp():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class SchemaMigration(Base):
    __tablename__ = "sys_schema_migration"
    version: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    checksum: Mapped[str]
    applied_at: Mapped[datetime] = timestamp()


class Page(Base):
    __tablename__ = "cfg_page"
    id: Mapped[int] = identity()
    page_code: Mapped[str]
    business_type: Mapped[str]
    created_by: Mapped[str]
    created_at: Mapped[datetime] = timestamp()


class PageVersion(Base):
    __tablename__ = "cfg_page_version"
    id: Mapped[int] = identity()
    page_id: Mapped[int] = mapped_column(BigInteger)
    version_no: Mapped[str]
    base_page_version_id: Mapped[int | None] = mapped_column(BigInteger)
    schema_version: Mapped[str]
    digest_version: Mapped[str]
    title: Mapped[str]
    region_code: Mapped[str]
    enabled: Mapped[bool]
    page_options: Mapped[dict[str, Any]]
    content_digest: Mapped[str]
    change_reason: Mapped[str]
    created_by: Mapped[str]
    created_at: Mapped[datetime] = timestamp()


class PageField(Base):
    __tablename__ = "cfg_page_field"
    id: Mapped[int] = identity()
    page_version_id: Mapped[int] = mapped_column(BigInteger)
    field_key: Mapped[str]
    label: Mapped[str]
    sort_order: Mapped[int]
    reviewable: Mapped[bool]
    required: Mapped[bool]
    writable: Mapped[bool]
    normalizer: Mapped[str]
    options: Mapped[dict[str, Any]]


class PageMaterial(Base):
    __tablename__ = "cfg_page_material"
    id: Mapped[int] = identity()
    page_version_id: Mapped[int] = mapped_column(BigInteger)
    material_code: Mapped[str]
    label: Mapped[str]
    sort_order: Mapped[int]
    guidance: Mapped[str]
    options: Mapped[dict[str, Any]]


class Release(Base):
    __tablename__ = "cfg_release"
    id: Mapped[int] = identity()
    release_no: Mapped[str]
    base_release_id: Mapped[int | None] = mapped_column(BigInteger)
    rollback_target_release_id: Mapped[int | None] = mapped_column(BigInteger)
    operation_type: Mapped[str]
    source: Mapped[str]
    source_session_id: Mapped[str | None]
    operation_id: Mapped[str]
    request_digest: Mapped[str]
    manifest_digest: Mapped[str]
    digest_version: Mapped[str]
    validation_run_id: Mapped[str]
    minimum_kernel_version: Mapped[str]
    created_by: Mapped[str]
    confirmed_by: Mapped[str]
    confirmed_at: Mapped[datetime]
    change_reason: Mapped[str]
    created_at: Mapped[datetime] = timestamp()


class ReleaseItem(Base):
    __tablename__ = "cfg_release_item"
    release_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_version_id: Mapped[int] = mapped_column(BigInteger)


class Head(Base):
    __tablename__ = "cfg_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = timestamp()


class Capture(Base):
    __tablename__ = "cfg_capture"
    id: Mapped[str] = mapped_column(primary_key=True)
    extension_id: Mapped[str]
    snapshot_id: Mapped[str]
    page_instance_id: Mapped[str]
    page_url: Mapped[str]
    title: Mapped[str]
    structure_digest: Mapped[str]
    snapshot_schema_version: Mapped[str]
    snapshot: Mapped[dict[str, Any]]
    created_by: Mapped[str]
    created_at: Mapped[datetime] = timestamp()
    expires_at: Mapped[datetime]


class ConfigurationSession(Base):
    __tablename__ = "cfg_session"
    id: Mapped[str] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(BigInteger)
    capture_id: Mapped[str | None]
    base_page_version_id: Mapped[int] = mapped_column(BigInteger)
    base_release_id: Mapped[int] = mapped_column(BigInteger)
    baseline_digest: Mapped[str]
    state: Mapped[str]
    turn_status: Mapped[str]
    revision: Mapped[int] = mapped_column(BigInteger)
    draft: Mapped[dict[str, Any]]
    draft_digest: Mapped[str]
    workflow_schema_version: Mapped[str]
    workflow: Mapped[dict[str, Any]]
    current_validation_run_id: Mapped[str | None]
    published_release_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str]
    created_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = timestamp()
    closed_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]


class ValidationRun(Base):
    __tablename__ = "cfg_validation_run"
    id: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[str | None]
    purpose: Mapped[str]
    base_release_id: Mapped[int | None] = mapped_column(BigInteger)
    candidate_page_id: Mapped[int | None] = mapped_column(BigInteger)
    candidate_revision: Mapped[int | None] = mapped_column(BigInteger)
    candidate_digest: Mapped[str | None]
    manifest_digest: Mapped[str]
    digest_version: Mapped[str]
    scene_digest: Mapped[str | None]
    kernel_version: Mapped[str]
    kernel_fingerprint: Mapped[str]
    report_schema_version: Mapped[str]
    status: Mapped[str]
    report: Mapped[dict[str, Any]]
    performed_by: Mapped[str]
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]


class SessionEvent(Base):
    __tablename__ = "cfg_session_event"
    seq: Mapped[int] = identity()
    session_id: Mapped[str]
    kind: Mapped[str]
    role: Mapped[str | None]
    content: Mapped[str | None]
    tool_call_id: Mapped[str | None]
    tool_name: Mapped[str | None]
    payload_schema_version: Mapped[str]
    payload: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = timestamp()


class Idempotency(Base):
    __tablename__ = "cfg_idempotency"
    operation_type: Mapped[str] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(primary_key=True)
    request_digest: Mapped[str]
    result: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = timestamp()
    expires_at: Mapped[datetime] = mapped_column(server_default=FetchedValue())


class VehicleModelKey(Base):
    __tablename__ = "ref_vehicle_model_key"
    model_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    announcement_code: Mapped[str | None]
    engine_code: Mapped[str | None]


class EngineEmission(Base):
    __tablename__ = "ref_engine_emission"
    engine_model: Mapped[str] = mapped_column(primary_key=True)
    emission_standard: Mapped[str]
    source_config_count: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = timestamp()


class DatasetImport(Base):
    __tablename__ = "ref_dataset_import"
    id: Mapped[int] = identity()
    dataset_code: Mapped[str]
    source_name: Mapped[str]
    source_environment: Mapped[str | None]
    source_exported_at: Mapped[datetime | None]
    source_tables: Mapped[list[str]] = mapped_column(ARRAY(Text))
    source_digest: Mapped[str]
    format_version: Mapped[str]
    row_count: Mapped[int] = mapped_column(BigInteger)
    builder_version: Mapped[str]
    source_row_count: Mapped[int | None] = mapped_column(BigInteger)
    excluded_ambiguous_count: Mapped[int | None] = mapped_column(BigInteger)
    excluded_invalid_count: Mapped[int | None] = mapped_column(BigInteger)
    imported_by: Mapped[str]
    imported_at: Mapped[datetime] = timestamp()


class VehicleBrand(Base):
    __tablename__ = "forj_vehicle_brand"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand_name: Mapped[str | None]
    del_flag: Mapped[int | None]


class VehicleSeries(Base):
    __tablename__ = "forj_vehicle_series"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int | None] = mapped_column(BigInteger)
    series_name: Mapped[str | None]
    del_flag: Mapped[int | None]


class VehicleModel(Base):
    __tablename__ = "forj_vehicle_model"
    model_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_name: Mapped[str | None]
    brand_id: Mapped[int | None] = mapped_column(BigInteger)
    s_id: Mapped[int | None] = mapped_column(BigInteger)
    series_id: Mapped[int | None] = mapped_column(BigInteger)
    type_level: Mapped[str | None]
    announcement_model: Mapped[str | None]
    del_flag: Mapped[int | None]
    operate: Mapped[int | None]


class VehicleModelConfig(Base):
    __tablename__ = "forj_vehicle_model_config"
    model_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    announcement_model: Mapped[str | None]
    drive_form: Mapped[str | None]
    fuel_type: Mapped[str | None]
    emission_standard: Mapped[str | None]
    max_hp: Mapped[str | None]
    engine_brand: Mapped[str | None]
    engine_type: Mapped[str | None]
    packing_case_length: Mapped[str | None]
    gearbox_model: Mapped[str | None]
    shift_way: Mapped[str | None]
    max_output: Mapped[str | None]
    weight_kg: Mapped[str | None]
    total_traction_mass: Mapped[str | None]
    rear_axle_ratio: Mapped[str | None]
