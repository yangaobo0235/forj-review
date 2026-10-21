"""Public service alias for the shared typed PostgreSQL settings."""

from forj_review_database.settings import (
    ConnectionSettings as ConnectionSettings,
)
from forj_review_database.settings import (
    database_url_from_config as database_url_from_config,
)
from forj_review_database.settings import (
    load_connection_settings as load_connection_settings,
)
