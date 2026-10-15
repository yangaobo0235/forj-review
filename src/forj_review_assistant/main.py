"""ASGI entrypoint; service construction belongs to bootstrap and lifespan."""
from forj_review_assistant.bootstrap.application import create_app

app = create_app()
