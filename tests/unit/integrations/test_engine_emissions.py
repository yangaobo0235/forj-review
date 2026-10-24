"""Exact JSON lookups, immutable snapshots, and explicit data failures."""

import asyncio

import pytest

from forj_review_service.contracts.engine_emissions import EngineEmissionLookupUnavailable
from forj_review_service.integrations.engine_emissions import (
    load_engine_emissions,
    lookup_engine_emissions,
)
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler


def test_reference_normalizes_exact_keys_and_remains_immutable(tmp_path):
    path = tmp_path / "emissions.json"
    path.write_text('{"ENGINE42-E6": "国六"}', encoding="utf-8")
    reference = load_engine_emissions(path)
    assert lookup_engine_emissions(
        ["示例 ＥＮＧＩＮＥ４２－Ｅ６", "ENGINE42-E6", "ENGINE42-E6A", ""], reference
    ) == {"ENGINE42-E6": "国六"}
    assert lookup_engine_emissions([], reference) == {}
    assert lookup_engine_emissions(["UNKNOWN-E6"], reference) == {}
    with pytest.raises(TypeError):
        reference["MODEL"] = "国三"
    path.write_text('{"ENGINE42-E6": "国三"}', encoding="utf-8")
    assert reference["ENGINE42-E6"] == "国六"


@pytest.mark.parametrize(
    "content",
    [
        "{}",
        "[]",
        "null",
        "broken",
        '{"MODEL":"京六"}',
        '{"MODEL":"国六","MODEL":"国三"}',
        '{"model":"国六","ＭＯＤＥＬ":"国三"}',
        '{"":"国六"}',
        '{"MODEL": []}',
    ],
)
def test_invalid_reference_is_not_an_ordinary_miss(tmp_path, content):
    path = tmp_path / "emissions.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(EngineEmissionLookupUnavailable):
        load_engine_emissions(path)


def test_missing_reference_is_explicit(tmp_path):
    with pytest.raises(EngineEmissionLookupUnavailable):
        load_engine_emissions(tmp_path / "absent.json")


@pytest.mark.asyncio
async def test_service_reference_is_safe_across_worker_loops(tmp_path, monkeypatch):
    path = tmp_path / "emissions.json"
    path.write_text('{"LOOPTEST":"国六"}', encoding="utf-8")
    monkeypatch.setenv("REVIEW_ENGINE_EMISSIONS_FILE", str(path))
    service = ReviewService(load_model=False)
    path.unlink()

    async def run():
        assert await service.lookup_engine_emissions(["LOOPTEST"]) == {"LOOPTEST": "国六"}

    schedulers = [FairAsyncScheduler(global_limit=1, per_job_limit=1) for _ in range(2)]
    try:
        await asyncio.gather(*(asyncio.wrap_future(s.submit("lookup", run)) for s in schedulers))
    finally:
        for scheduler in schedulers:
            await asyncio.to_thread(scheduler.close, cleanup=service.aclose)
