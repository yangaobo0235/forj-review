"""A reload compiles before swap; existing jobs own their original service."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from forj_review_service.runtime.configuration.reloader import ConfigurationReloader
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import business_registry


@pytest.mark.asyncio
async def test_failed_compile_preserves_service_then_success_swaps_whole_snapshot(monkeypatch):
    original = ReviewService(load_model=False, registry=business_registry())
    original.registry.release_id = 1
    desired = 2

    class Database:
        @asynccontextmanager
        async def session(self, **kwargs):
            yield self

        async def scalar(self, query):
            return desired

    app = SimpleNamespace(state=SimpleNamespace(review_service=original,
                          review_jobs=SimpleNamespace(review_service=original)))
    loader = ConfigurationReloader(app, Database())

    async def invalid(database):
        raise ValueError("invalid page reference")

    monkeypatch.setattr("forj_review_service.runtime.configuration.reloader.load_registry", invalid)
    with pytest.raises(ValueError):
        await loader.refresh()
    assert app.state.review_service is original
    assert app.state.review_jobs.review_service is original
    replacement = business_registry()
    replacement.release_id = desired

    async def valid(database):
        return replacement

    monkeypatch.setattr("forj_review_service.runtime.configuration.reloader.load_registry", valid)
    assert await loader.refresh()
    loaded = app.state.review_service
    assert loaded is app.state.review_jobs.review_service and loaded is not original
    assert loaded.registry is replacement and loaded.workflow is not original.workflow
    assert not await loader.refresh()

