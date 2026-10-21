"""Versioned storage contract shared by both service startup checks."""

import hashlib
from pathlib import Path

BASELINE_VERSION = 4
# Published v4 is immutable. Never recalculate this pin to accept an edited baseline.
BASELINE_CHECKSUM = "1b1b962827b6d25a9c53eb7ecb0173a6b359d002db59f7d0ea9ab731fca7d5ed"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
TABLES = (
    "sys_schema_migration",
    "cfg_page",
    "cfg_page_version",
    "cfg_page_field",
    "cfg_page_material",
    "cfg_release",
    "cfg_release_item",
    "cfg_head",
    "cfg_validation_run",
    "cfg_capture",
    "cfg_session",
    "cfg_session_event",
    "cfg_idempotency",
    "ref_vehicle_model_key",
    "ref_engine_emission",
    "ref_dataset_import",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "forj_vehicle_brand",
    "forj_vehicle_series",
    "forj_vehicle_model",
    "forj_vehicle_model_config",
)


def schema_checksum():
    checksum = hashlib.sha256(SCHEMA_SQL.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if checksum != BASELINE_CHECKSUM:
        raise ValueError("已发布的 v4 基线被修改，请恢复 schema.sql 并追加迁移")
    return checksum
