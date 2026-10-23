"""Private database config, explicit overrides and failure before any remote writes."""

import asyncio
import os
import sys

import pytest
from psycopg.conninfo import conninfo_to_dict

from forj_review_config_service.config import database as configuration_database
from forj_review_config_service.config.settings import load_settings
from forj_review_service.config import database as review_database_module
from forj_review_service.integrations.database import review_database


@pytest.fixture(
    params=[configuration_database, review_database_module], ids=["configuration", "review"]
)
def load_connection_settings(request):
    return request.param.load_connection_settings


def test_shared_config_handles_password_characters_without_url_encoding(
    tmp_path, monkeypatch, load_connection_settings
):
    path = tmp_path / ".env.database"
    password = " p@ss#%\\'word= "
    escaped = password.replace("\\", "\\\\").replace('"', '\\"')
    path.write_text(
        "PGHOST=database.example\nPGUSER=existing_user\n"
        f'PGPASSWORD="{escaped}"\nPGDATABASE=assistant\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(os, "environ", {})
    settings = load_connection_settings(path)
    info = conninfo_to_dict(settings.conninfo())
    assert info["password"] == password
    assert info["host"] == "database.example" and info["dbname"] == "assistant"
    assert password not in repr(settings)


def test_both_backends_read_shared_config_and_explicit_urls_win(tmp_path, monkeypatch):
    path = tmp_path / ".env.database"
    path.write_text("PGHOST=db.example\nPGPASSWORD=test-only\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", {"DATABASE_CONFIG_FILE": str(path)})
    monkeypatch.setattr("forj_review_service.integrations.database.load_dotenv", lambda: None)
    configuration = load_settings(tmp_path / "no-model.env")
    review = review_database()
    assert conninfo_to_dict(review.url) == conninfo_to_dict(configuration.database_url)
    assert review.schema == configuration.database_schema == "local"
    assert conninfo_to_dict(review.url)["dbname"] == "forj_review"
    assert conninfo_to_dict(review.url)["user"] == "postgres"
    monkeypatch.setenv("REVIEW_DATABASE_URL", "postgresql://override@other/other")
    monkeypatch.setenv("REVIEW_DATABASE_SCHEMA", "custom_schema")
    monkeypatch.setenv(
        "CONFIGURATION_DATABASE_URL", "postgresql://configuration@other/configuration"
    )
    assert review_database().url == "postgresql://override@other/other"
    assert review_database().schema == "custom_schema"
    assert (
        load_settings(tmp_path / "no-model.env").database_url
        == "postgresql://configuration@other/configuration"
    )


def test_environment_overrides_private_file_and_empty_password_fails_closed(
    tmp_path, monkeypatch, load_connection_settings
):
    path = tmp_path / ".env.database"
    path.write_text("PGHOST=file.example\nPGPASSWORD=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("PGHOST", "environment.example")
    assert load_connection_settings(path).host == "environment.example"
    monkeypatch.setenv("PGPASSWORD", "")
    with pytest.raises(ValueError, match="PGPASSWORD"):
        load_connection_settings(path)


def test_missing_configuration_fails_before_connecting(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"DATABASE_CONFIG_FILE": str(tmp_path / "missing.env")})
    monkeypatch.setattr("forj_review_service.integrations.database.load_dotenv", lambda: None)
    with pytest.raises(ValueError, match="PGPASSWORD"):
        review_database()
    with pytest.raises(ValueError, match="PGPASSWORD"):
        load_settings(tmp_path / "no-model.env")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop compatibility")
def test_unsupported_windows_loop_reports_startup_fix_before_connecting(monkeypatch):
    from forj_review_database.database import AsyncDatabase, Database

    async def probe():
        async with AsyncDatabase(Database("host=127.0.0.1 port=1 dbname=unused")) as database:

            def unexpected_connection(**kwargs):
                pytest.fail("An unsupported event loop must fail before opening a connection")

            monkeypatch.setattr(database, "session", unexpected_connection)
            with pytest.raises(ValueError, match="--loop asyncio:SelectorEventLoop"):
                await database.require_ready()

    with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
        runner.run(probe())
