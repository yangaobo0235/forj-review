"""Exact engine model keys and lookup protocols for reference table evidence."""

import re
import unicodedata
from collections.abc import Iterable
from typing import Protocol

EMISSION_STANDARDS = frozenset({"国三", "国四", "国五", "国六"})


class EngineEmissionLookupUnavailable(Exception):
    """参考数据不可读取，不能当作普通未命中。"""


class AsyncEngineEmissionLookup(Protocol):
    async def __call__(self, models: Iterable[str]) -> dict[str, str]: ...


def engine_model_key(value: object | None, *, engine_brand: str | None = None) -> str:
    """Remove formatting and a leading brand label, retaining the complete model suffix."""
    text = str(value or "").strip()
    brand = (engine_brand or "").strip()
    if brand and text.startswith(brand):
        text = text[len(brand) :]
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).upper()
    return re.sub(r"^[^A-Z0-9]+", "", text)
