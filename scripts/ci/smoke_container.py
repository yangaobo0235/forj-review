"""Smoke-test the packaged audit service with networking completely disabled."""

import json
import subprocess
import sys
import time
from uuid import uuid4


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def request(name: str, path: str, payload=None):
    script = """
import json, sys, urllib.request, urllib.error
body = None if sys.argv[2] == 'null' else sys.argv[2].encode()
req = urllib.request.Request('http://127.0.0.1:26101' + sys.argv[1], body, {'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        print(json.dumps({'status':response.status, 'body':json.load(response)}))
except urllib.error.HTTPError as error:
    print(json.dumps({'status':error.code, 'body':json.load(error)}))
"""
    return json.loads(docker("exec", name, "python", "-c", script, path, json.dumps(payload)))


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else "forj-review-audit:master"
    name = "review-audit-smoke-" + uuid4().hex[:12]
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "--tmpfs",
            "/logs:size=16m,uid=10001,gid=10001,mode=0770",
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
        for _ in range(40):
            try:
                assert request(name, "/health")["body"] == {"status": "ok"}
                break
            except subprocess.CalledProcessError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Container health timeout")
        docker(
            "exec",
            name,
            "python",
            "-c",
            'import importlib.util; assert all(importlib.util.find_spec(m) is None for m in ("psycopg", "sqlalchemy", "forj_review_database", "forj_review_config_service"))',
        )
        loaded = request(name, "/api/review/configuration/loaded")["body"]
        assert loaded["configuration_source"] == "json" and len(loaded["pages"]) == 5
        assert request(name, "/api/review/configuration/schema")["status"] == 404
        assert request(name, "/api/review/configuration/rules")["status"] == 404
        catalog = request(name, "/api/review/page-catalog")["body"]
        for page in catalog["identities"]:
            result = request(
                name,
                "/api/review/assist",
                {
                    "page_url": "https://example.test/review",
                    "business_type": page["business_type"],
                    "region": page["region"],
                    "page_id": page.get("page_id", ""),
                    "profile_version": page.get("profile_version", "1.0"),
                    "images": [],
                    "trace_id": "smoke-trace",
                },
            )
            assert result["status"] == 200, result
            assert result["body"]["recommendation"] == "REVIEW_REQUIRED"
        assert request(name, "/private-test?token=SECRET")["status"] == 404
        for content in (
            docker("exec", name, "cat", "/logs/review-service.log"),
            docker("logs", name),
        ):
            for event in (
                "review service started",
                "review node completed",
                "review capability completed",
                "review decision",
                "review http completed",
            ):
                assert event in content, event
            assert "trace_id=smoke-trace" in content
            assert all(
                secret not in content for secret in ("SECRET", "example.test", "private-test")
            )
        assert docker("exec", name, "id", "-u") == "10001"
        assert docker("exec", name, "stat", "-c", "%a", "/logs/review-service.log") == "640"
        docker("stop", "--time=20", name)
        log = docker("logs", name)
        assert "review service stopped" in log and "Application shutdown complete" in log
        assert "Application shutdown failed" not in log and "Event loop is closed" not in log
        print(
            "Audit container smoke: no network/database, all business routes, logs, permissions and shutdown OK"
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


if __name__ == "__main__":
    main()
