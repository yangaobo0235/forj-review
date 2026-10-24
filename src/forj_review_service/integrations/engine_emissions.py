"""Immutable, exact engine reference lookup; unavailable data is never a no-match."""

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

from forj_review_service.contracts.engine_emissions import (
    EMISSION_STANDARDS,
    EngineEmissionLookupUnavailable,
    engine_model_key,
)


def _reference(pairs):
    result = {}
    for model, standard in pairs:
        key = engine_model_key(model)
        if not key or key in result or standard not in EMISSION_STANDARDS:
            raise ValueError("Invalid engine reference entry")
        result[key] = standard
    return result


def load_engine_emissions(path: Path) -> Mapping[str, str]:
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reference)
        if not isinstance(values, dict) or not values:
            raise ValueError("Empty or invalid engine reference")
    except (OSError, ValueError, TypeError) as exc:
        raise EngineEmissionLookupUnavailable(
            "发动机排放参考文件不可用，请检查服务数据文件"
        ) from exc
    return MappingProxyType(values)


def lookup_engine_emissions(models: Iterable[str], reference: Mapping[str, str]) -> dict[str, str]:
    keys = {engine_model_key(model) for model in models}
    return {key: reference[key] for key in keys if key in reference}
