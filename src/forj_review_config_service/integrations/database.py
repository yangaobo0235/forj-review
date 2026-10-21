"""Configuration service identity on the shared database connection contract."""

from dataclasses import dataclass

from forj_review_database.database import Database as SharedDatabase


@dataclass(frozen=True)
class Database(SharedDatabase):
    application_name: str = "forj-review-config-service"
