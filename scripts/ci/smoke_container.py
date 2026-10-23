"""Smoke-test a built Linux image without model credentials or external requests."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import psycopg


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else "forj-review-service:main"
    name = "review-smoke-" + uuid4().hex[:12]
    pg_name, network = name + "-postgres", name + "-network"
    stack = ExitStack()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tests.support.postgres import isolated_database, seed_complete_pages

        docker("network", "create", network)
        docker(
            "run",
            "--rm",
            "-d",
            "--name",
            pg_name,
            "--network",
            network,
            "-e",
            "POSTGRES_PASSWORD=smoke-only",
            "-p",
            "127.0.0.1::5432",
            "postgres:16-alpine",
        )
        pg_port = docker("port", pg_name, "5432/tcp").rsplit(":", 1)[-1]
        pg_url = f"postgresql://postgres:smoke-only@127.0.0.1:{pg_port}/postgres"
        for _ in range(60):
            try:
                with psycopg.connect(pg_url, connect_timeout=1):
                    break
            except psycopg.OperationalError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Smoke PostgreSQL timeout")
        database = stack.enter_context(isolated_database(pg_url))
        seed_complete_pages(database)
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "-e",
            "PGHOST=" + pg_name,
            "-e",
            "PGPORT=5432",
            "-e",
            "PGDATABASE=postgres",
            "-e",
            "PGUSER=postgres",
            "-e",
            "PGPASSWORD=smoke-only",
            "-e",
            "PGSSLMODE=disable",
            "-e",
            "REVIEW_DATABASE_SCHEMA=" + database.schema,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "--tmpfs",
            "/logs:size=16m,uid=10001,gid=10001,mode=0770",
            "-p",
            "127.0.0.1::26101",
            "-e",
            "DASHSCOPE_API_KEY=",
            "-e",
            "REVIEW_LOG_DIR=/logs",
            "-e",
            "REVIEW_LOG_REQUIRED=true",
            "-e",
            "TZ=Asia/Shanghai",
            image,
        )
        port = docker("port", name, "26101/tcp").rsplit(":", 1)[-1]
        base = f"http://127.0.0.1:{port}"
        for _ in range(40):
            try:
                with opener.open(base + "/health", timeout=2) as response:
                    assert json.load(response) == {"status": "ok"}
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
        else:
            raise RuntimeError("Container health timeout")
        with opener.open(base + "/api/review/configuration/loaded", timeout=5) as response:
            assert json.load(response)["configuration_source"] == "postgresql:" + database.schema
        payload = {
            "page_url": "https://example.test/review/synthetic",
            "region": "qingdao",
            "images": [],
            "trace_id": "smoke-trace",
        }
        request = urllib.request.Request(
            base + "/api/review/assist",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=20) as response:
            body = json.load(response)
        assert body["recommendation"] == "REVIEW_REQUIRED"
        assert body["trace_id"] == "smoke-trace"
        try:
            opener.open(base + "/private-test?token=SECRET", timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        file_log = docker("exec", name, "cat", "/logs/review-service.log")
        console_log = docker("logs", name)
        for content in (file_log, console_log):
            for event in (
                "review service started",
                "review node completed",
                "review capability completed",
                "review decision",
                "review http completed",
            ):
                assert event in content, f"Missing {event}"
            assert "trace_id=smoke-trace" in content
            assert (
                "SECRET" not in content
                and "example.test" not in content
                and "private-test" not in content
            )
        assert docker("exec", name, "id", "-u") == "10001"
        assert docker("exec", name, "stat", "-c", "%a", "/logs/review-service.log") == "640"
        docker("stop", "--time=20", name)
        shutdown_log = docker("logs", name)
        assert "review service stopped" in shutdown_log
        assert "Application shutdown complete" in shutdown_log
        assert "Application shutdown failed" not in shutdown_log
        assert "Event loop is closed" not in shutdown_log
        print(
            "Container smoke: health, real graph/capabilities, correlated logs, "
            "non-root/read-only permissions and graceful shutdown OK"
        )
    except BaseException:
        try:
            print(docker("logs", "--tail=40", name), file=sys.stderr)
        except subprocess.CalledProcessError:
            pass
        raise
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            stack.close()
        finally:
            subprocess.run(
                ["docker", "rm", "-f", pg_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                ["docker", "network", "rm", network],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


if __name__ == "__main__":
    main()
