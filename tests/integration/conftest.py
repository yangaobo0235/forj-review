import pytest
from fastapi.testclient import TestClient

from forj_review_service.bootstrap.application import create_app
from forj_review_service.runtime.review_service import ReviewService
from tests.support.pages import business_registry


@pytest.fixture
def client():
    service = ReviewService(registry=business_registry(), load_model=False)
    with TestClient(create_app(review_service=service)) as connection:
        yield connection
