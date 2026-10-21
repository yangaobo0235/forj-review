import pytest
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app


@pytest.fixture
def client(database):
    from tests.support.postgres import seed_complete_pages

    seed_complete_pages(database)
    with TestClient(create_app()) as connection:
        yield connection
