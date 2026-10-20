"""测试收集策略。

历史上这里用一个子串名单把「过户业务」的旧夹具整体跳过。那些测试在扩展包
收口后已经完全失效（断言的是已改名的方法、已清空的策略和已停用的业务），
留着只会掩盖真实失败，因此已随本次重构删除。**不要再引入按名字静默跳过的
机制**——需要停用的测试直接删掉，或写成显式的 `pytest.mark.skip`。
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

# 测试进程不能往开发者的日志文件里写：`configure_log_file` 会读 `.env` 里的
# `REVIEW_LOG_DIR`，混进去的测试行会污染按天聚合的性能数据。这里先占位成一个
# 临时目录——`load_dotenv` 用 `setdefault`，已存在的进程环境变量优先级更高，
# 不会被 `.env` 覆盖。conftest 先于任何测试模块导入，因此也先于 `forj_review_service.main`。
os.environ.setdefault("REVIEW_LOG_DIR", tempfile.mkdtemp(prefix="review-service-test-logs-"))


@pytest.fixture(scope="session")
def postgres_url():
    """Tests own a loopback-only database, never the private server connection."""
    name = None
    url = os.getenv("FORJ_TEST_POSTGRES_URL")
    if not url:
        name = "forj-pytest-" + uuid4().hex[:10]
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                name,
                "-e",
                "POSTGRES_PASSWORD=test-only",
                "-p",
                "127.0.0.1::5432",
                "postgres:16-alpine",
            ],
            check=True,
            capture_output=True,
        )
        port = (
            subprocess.check_output(["docker", "port", name, "5432/tcp"], text=True)
            .strip()
            .rsplit(":", 1)[1]
        )
        url = f"postgresql://postgres:test-only@127.0.0.1:{port}/postgres"
    if conninfo_to_dict(url).get("host") not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("FORJ_TEST_POSTGRES_URL must use an isolated loopback PostgreSQL instance")
    try:
        for _ in range(60):
            try:
                with psycopg.connect(url, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                time.sleep(0.2)
        else:
            pytest.fail("Isolated test PostgreSQL did not become ready")
        yield url
    finally:
        if name:
            subprocess.run(["docker", "stop", name], capture_output=True, check=False)


@pytest.fixture(scope="session", autouse=True)
def postgres_event_loop_policy():
    previous = asyncio.get_event_loop_policy()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    yield
    asyncio.set_event_loop_policy(previous)


def pytest_asyncio_loop_factories(config, item):
    """psycopg requires selector loops on Windows, including async fixtures."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


@pytest.fixture
def database(postgres_url):
    from tests.support.postgres import isolated_database

    with isolated_database(postgres_url) as db:
        yield db


@pytest.fixture(autouse=True)
def isolate_private_database_settings(tmp_path, monkeypatch, request):
    # Filling the developer's real password must not send ordinary tests to their server.
    from forj_review_config_service.config import database as configuration_database
    from forj_review_service.config import database as review_database

    for module in (configuration_database, review_database):
        monkeypatch.setattr(module, "DEFAULT_DATABASE_FILE", tmp_path / "no-private-database.env")
        for key in module.SETTING_NAMES:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_CONFIG_FILE", str(tmp_path / "no-private-database.env"))
    database = request.getfixturevalue("database") if "database" in request.fixturenames else None
    for prefix in ("REVIEW", "CONFIGURATION"):
        monkeypatch.setenv(
            prefix + "_DATABASE_URL",
            database.url if database else "postgresql://postgres@127.0.0.1:1/disabled",
        )
        monkeypatch.setenv(
            prefix + "_DATABASE_SCHEMA", database.schema if database else "test_disabled"
        )
