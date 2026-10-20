import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app


@pytest_asyncio.fixture
async def async_database(database):
    from forj_review_config_service.integrations.async_database import AsyncDatabase

    async with AsyncDatabase(database) as runtime:
        await runtime.acquire_executor()
        yield runtime


@pytest.fixture
def client(database):
    with TestClient(create_app()) as connection:
        yield connection
