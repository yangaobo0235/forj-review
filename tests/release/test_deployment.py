"""Exercise real Bash entrypoints against a simulated Docker host, never the cloud."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def bash_executable():
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    candidate = Path(git).resolve().parents[1] / "bin/bash.exe" if git else None
    return str(candidate) if candidate and candidate.is_file() else None


DOCKER = r'''#!/usr/bin/env bash
set -eu
echo "$*" >> "$FAKE_STATE/commands"
if [[ $1 == info ]]; then exit 0; fi
if [[ $1 == compose ]]; then
  if [[ " $* " == *" up "* ]]; then
    cp "$FAKE_STATE/main" "$FAKE_STATE/container"
    echo recreated >> "$FAKE_STATE/recreates"
  fi
  exit 0
fi
if [[ $1 == load ]]; then cp "$FAKE_STATE/incoming" "$FAKE_STATE/main"; exit 0; fi
if [[ $1 == tag ]]; then
  case "$3" in
    *:rollback) echo "$2" > "$FAKE_STATE/rollback";;
    *:main) cat "$FAKE_STATE/rollback" > "$FAKE_STATE/main";;
    *) exit 1;;
  esac
  exit 0
fi
if [[ $1 == inspect ]]; then cat "$FAKE_STATE/container" 2>/dev/null; exit $?; fi
if [[ $1 == image && $2 == inspect ]]; then
  case "${@: -1}" in
    *:rollback) cat "$FAKE_STATE/rollback";;
    forj-review-api:main) cat "$FAKE_STATE/main";;
    *) exit 1;;
  esac
  exit $?
fi
if [[ $1 == run ]]; then exit 0; fi
exit 1
'''


@pytest.fixture
def host(tmp_path):
    bash = bash_executable()
    if not bash:
        pytest.skip("Deployment requires Bash (Git Bash on Windows)")
    upload, runtime, state, commands = [tmp_path / name for name in ("upload", "runtime", "state", "bin")]
    for directory in (upload, runtime, state, commands):
        directory.mkdir()
    (runtime / ".env").write_text("DASHSCOPE_API_KEY=synthetic\n", encoding="utf-8")
    for name in ("start-review.sh", "stop-review.sh", "rollback-review.sh", "review-common.sh"):
        (upload / name).write_text((ROOT / "deploy" / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    # flock itself is tested by Linux; Git Bash does not ship it.
    for name, text in {
        "docker": DOCKER,
        "curl": '#!/usr/bin/env bash\n[[ ! -f "$FAKE_STATE/unhealthy" ]]\n',
        "sleep": "#!/usr/bin/env bash\nexit 0\n",
        **({"flock": "#!/usr/bin/env bash\nexit 0\n"} if os.name == "nt" else {}),
    }.items():
        path = commands / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    class Host:
        def release(self, image="new", compose="new config"):
            (state / "incoming").write_text(image, encoding="utf-8")
            (upload / "forj-review-api-main.tar").write_text("synthetic image archive", encoding="utf-8")
            (upload / "docker-compose.server.yml").write_text(compose, encoding="utf-8")
            sums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                           for path in sorted(upload.iterdir()) if path.name not in {"SHA256SUMS", "deploy.log", "deploy.log.1"} and path.is_file())
            (upload / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")

        def run(self, name, *, success=True):
            env = {**os.environ, "REVIEW_UPLOAD_DIR": upload.as_posix(), "REVIEW_RUNTIME_DIR": runtime.as_posix(),
                   "FAKE_STATE": state.as_posix(), "TEST_BIN": commands.as_posix()}
            result = subprocess.run([bash, "-c", 'if command -v cygpath >/dev/null; then TEST_BIN=$(cygpath -u "$TEST_BIN"); fi; export PATH="$TEST_BIN:$PATH"; exec bash "$REVIEW_UPLOAD_DIR/$1"', "test", name],
                                    env=env, capture_output=True, text=True, timeout=20)
            assert (result.returncode == 0) == success, result.stdout + result.stderr
            return result.stdout + result.stderr

    item = Host()
    item.upload, item.runtime, item.state = upload, runtime, state
    return item


def test_upgrade_repeat_failure_retry_and_rollback_keep_the_healthy_pair(host):
    host.release("old", "old config")
    host.run("start-review.sh")
    assert not (host.state / "rollback").exists()
    host.release("new", "new config")
    host.run("start-review.sh")
    assert (host.state / "rollback").read_text().strip() == "old"
    assert (host.runtime / "rollback-compose.yml").read_text() == "old config"
    host.run("start-review.sh")
    assert (host.state / "rollback").read_text().strip() == "old"
    host.release("broken", "broken config")
    (host.state / "unhealthy").touch()
    host.run("start-review.sh", success=False)
    host.run("start-review.sh", success=False)
    assert (host.state / "rollback").read_text().strip() == "new"
    assert (host.runtime / "rollback-compose.yml").read_text() == "new config"
    (host.state / "unhealthy").unlink()
    host.run("rollback-review.sh")
    assert (host.state / "container").read_text().strip() == "new"
    assert (host.runtime / "docker-compose.server.yml").read_text() == "new config"
    assert (host.runtime / ".last-successful-image").read_text().strip() == "new"


def test_checksum_failure_prevents_image_load_or_container_changes(host):
    host.release()
    (host.upload / "forj-review-api-main.tar").write_text("corrupt", encoding="utf-8")
    assert "checksum verification failed" in host.run("start-review.sh", success=False)
    commands = (host.state / "commands").read_text()
    assert "load --input" not in commands and " up " not in commands


def test_migrate_existing_container_and_stop_preserve_data(host):
    (host.state / "container").write_text("legacy", encoding="utf-8")
    (host.runtime / "docker-compose.server.yml").write_text("legacy config", encoding="utf-8")
    host.release()
    host.run("start-review.sh")
    assert (host.state / "rollback").read_text().strip() == "legacy"
    host.run("stop-review.sh")
    commands = (host.state / "commands").read_text()
    assert "stop review-agent" in commands
    assert " down" not in commands and " rmi" not in commands and "prune" not in commands
    assert (host.runtime / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"


def test_missing_previous_compose_refuses_incomplete_rollback(host):
    (host.state / "container").write_text("legacy", encoding="utf-8")
    host.release()
    assert "Compose file is missing" in host.run("start-review.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()


def test_entrypoint_shell_syntax():
    bash = bash_executable()
    if not bash:
        pytest.skip("Bash is unavailable")
    for script in (ROOT / "deploy").glob("*.sh"):
        result = subprocess.run([bash, "-n"], input=script.read_text(encoding="utf-8"), text=True, capture_output=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"
