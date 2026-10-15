import pytest
from fastapi.testclient import TestClient

from forj_review_assistant.bootstrap.application import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as connection:
        yield connection
