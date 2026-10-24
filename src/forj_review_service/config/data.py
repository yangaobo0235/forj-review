"""Read-only audit rules and reference data, packaged with the service."""

import os
from pathlib import Path

from forj_review_service.config.settings import load_dotenv

DEFAULT_DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"


def pages_directory() -> Path:
    load_dotenv()
    return Path(os.getenv("REVIEW_PAGES_DIR") or DEFAULT_DATA_DIRECTORY / "pages")


def engine_emissions_file() -> Path:
    load_dotenv()
    return Path(
        os.getenv("REVIEW_ENGINE_EMISSIONS_FILE")
        or DEFAULT_DATA_DIRECTORY / "engine-emissions.json"
    )
