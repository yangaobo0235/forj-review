"""Smoke-test a built Linux image without model credentials or external requests."""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else "forj-review-api:main"
    name = "review-smoke-" + uuid4().hex[:12]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        docker("run", "-d", "--name", name, "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges:true", "--tmpfs", "/tmp:size=64m,mode=1777",
               "--tmpfs", "/logs:size=16m,uid=10001,gid=10001,mode=0770",
               "-p", "127.0.0.1::8010", "-e", "DASHSCOPE_API_KEY=", "-e", "REVIEW_LOG_DIR=/logs",
               "-e", "REVIEW_LOG_REQUIRED=true", "-e", "TZ=Asia/Shanghai", image)
        port = docker("port", name, "8010/tcp").rsplit(":", 1)[-1]
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
        payload = {"page_url": "https://example.test/review/synthetic", "region": "qingdao", "images": [], "trace_id": "smoke-trace"}
        request = urllib.request.Request(base + "/api/review/assist", json.dumps(payload).encode(), {"Content-Type": "application/json"})
        with opener.open(request, timeout=20) as response:
            body = json.load(response)
        assert body["recommendation"] == "REVIEW_REQUIRED"
        assert body["trace_id"] == "smoke-trace"
        try:
            opener.open(base + "/private-test?token=SECRET", timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        file_log = docker("exec", name, "cat", "/logs/review-agent.log")
        console_log = docker("logs", name)
        for content in (file_log, console_log):
            for event in ("review service started", "review node completed", "review capability completed", "review decision", "review http completed"):
                assert event in content, f"Missing {event}"
            assert "trace_id=smoke-trace" in content
            assert "SECRET" not in content and "example.test" not in content and "private-test" not in content
        assert docker("exec", name, "id", "-u") == "10001"
        assert docker("exec", name, "stat", "-c", "%a", "/logs/review-agent.log") == "640"
        print("Container smoke: health, real graph/capabilities, correlated console/file logs, non-root/read-only and permissions OK")
    except BaseException:
        try:
            print(docker("logs", "--tail=40", name), file=sys.stderr)
        except subprocess.CalledProcessError:
            pass
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
