"""Review service database composition; common lifecycle lives in forj_review_database."""

import os

from forj_review_database.database import AsyncDatabase as AsyncDatabase
from forj_review_database.database import Database as Database
from forj_review_service.config.database import database_url_from_config
from forj_review_service.config.settings import load_dotenv


def review_database() -> Database:
    load_dotenv()
    url = os.getenv("REVIEW_DATABASE_URL", "").strip() or database_url_from_config()
    return Database(url, os.getenv("REVIEW_DATABASE_SCHEMA", "local"))
