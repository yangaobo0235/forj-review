"""Exercise real Bash entrypoints against a simulated Docker host, never the cloud."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def image_id(name):
    return "sha256:" + hashlib.sha256(name.encode()).hexdigest()


def bash_path(path):
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


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
if [[ $1 == inspect ]]; then
  if [[ ${2:-} == --format ]]; then cat "$FAKE_STATE/container" 2>/dev/null;
  else cat "$FAKE_STATE/container.json" 2>/dev/null; fi
  exit $?
fi
if [[ $1 == image && $2 == inspect ]]; then
  case "${@: -1}" in
    *:rollback) cat "$FAKE_STATE/rollback";;
    forj-review-api:main) cat "$FAKE_STATE/main";;
    *) exit 1;;
  esac
  exit $?
fi
if [[ $1 == run ]]; then
  if [[ " $* " == *" --log-driver none "* ]]; then "$TEST_PYTHON" -c "${@: -1}"; exit $?; fi
  exit 0
fi
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
    shutil.copyfile(ROOT / "deploy/recover-legacy-compose.py", upload / "recover-legacy-compose.py")
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
        def write_archive(self):
            with tarfile.open(upload / "forj-review-server.tar.gz", "w:gz") as archive:
                for name, content in self.members.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))

        def release(self, image="new", compose="new config"):
            (state / "incoming").write_text(image_id(image), encoding="utf-8")
            if compose is not None:
                (upload / "docker-compose.server.yml").write_text(compose, encoding="utf-8")
            self.members = {"forj-review-api-main.tar": ("synthetic " + image).encode(),
                            "release.json": (json.dumps({"image_id": image_id(image)}, indent=2) + "\n").encode()}
            self.members["SHA256SUMS"] = "".join(
                f"{hashlib.sha256(content).hexdigest()}  {name}\n"
                for name, content in self.members.items()
            ).encode()
            self.write_archive()

        def run(self, name, *, success=True):
            env = {**os.environ, "REVIEW_UPLOAD_DIR": bash_path(upload), "REVIEW_RUNTIME_DIR": bash_path(runtime),
                   "FAKE_STATE": bash_path(state), "TEST_BIN": bash_path(commands),
                   "TEST_PYTHON": bash_path(Path(sys.executable))}
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
    assert (host.state / "rollback").read_text().strip() == image_id("old")
    assert (host.runtime / "rollback-compose.yml").read_text() == "old config"
    host.run("start-review.sh")
    assert (host.state / "rollback").read_text().strip() == image_id("old")
    host.release("broken", "broken config")
    (host.state / "unhealthy").touch()
    host.run("start-review.sh", success=False)
    host.run("start-review.sh", success=False)
    assert (host.state / "rollback").read_text().strip() == image_id("new")
    assert (host.runtime / "rollback-compose.yml").read_text() == "new config"
    (host.state / "unhealthy").unlink()
    host.run("rollback-review.sh")
    assert (host.state / "container").read_text().strip() == image_id("new")
    assert (host.runtime / "docker-compose.server.yml").read_text() == "new config"
    assert (host.runtime / ".last-successful-image").read_text().strip() == image_id("new")


def test_checksum_failure_prevents_image_load_or_container_changes(host):
    host.release()
    host.members["forj-review-api-main.tar"] = b"corrupt"
    host.write_archive()
    assert "checksum verification failed" in host.run("start-review.sh", success=False)
    commands = (host.state / "commands").read_text()
    assert "load --input" not in commands and " up " not in commands


def test_migrate_existing_container_and_stop_preserve_data(host):
    (host.state / "container").write_text(image_id("legacy"), encoding="utf-8")
    (host.runtime / "docker-compose.server.yml").write_text("legacy config", encoding="utf-8")
    host.release()
    host.run("start-review.sh")
    assert (host.state / "rollback").read_text().strip() == image_id("legacy")
    host.run("stop-review.sh")
    commands = (host.state / "commands").read_text()
    assert "stop review-agent" in commands
    assert " down" not in commands and " rmi" not in commands and "prune" not in commands
    assert (host.runtime / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"


def test_missing_previous_compose_without_container_refuses_incomplete_rollback(host):
    (host.state / "main").write_text(image_id("legacy"), encoding="utf-8")
    host.release()
    assert "Compose file is missing" in host.run("start-review.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()


def test_missing_legacy_compose_recovered_from_container_and_can_rollback(host):
    (host.state / "container").write_text(image_id("legacy"), encoding="utf-8")
    container = {
        "Name": "/review-agent-service", "Image": image_id("legacy"),
        "Config": {"Env": ["DASHSCOPE_API_KEY=old-synthetic-$value"], "Cmd": ["old-server"]},
        "HostConfig": {"NetworkMode": "bridge", "RestartPolicy": {"Name": "unless-stopped"},
                       "PortBindings": {"8010/tcp": [{"HostIp": "", "HostPort": "18110"}]}},
        "Mounts": [{"Type": "bind", "Source": "/legacy/logs", "Destination": "/logs", "RW": True}],
    }
    (host.state / "container.json").write_text(json.dumps([container]), encoding="utf-8")
    host.release()
    output = host.run("start-review.sh")
    assert "Legacy runtime definition recovered" in output
    assert "old-synthetic" not in output
    old_compose = json.loads((host.runtime / "rollback-compose.yml").read_text())
    old_service = old_compose["services"]["review-agent"]
    assert old_service["image"] == image_id("legacy")
    assert old_service["environment"] == ["DASHSCOPE_API_KEY=old-synthetic-$$value"]
    assert old_service["volumes"][0]["source"] == "/legacy/logs"
    assert old_service["ports"][0]["published"] == "18110"
    if os.name != "nt":
        assert (host.runtime / "rollback-compose.yml").stat().st_mode & 0o777 == 0o600
    assert (host.runtime / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"
    assert not list(host.upload.glob(".release-*"))
    host.run("rollback-review.sh")
    assert (host.state / "container").read_text().strip() == image_id("legacy")


def test_unsupported_legacy_recovery_never_replaces_runtime_or_loads_image(host):
    (host.state / "container").write_text(image_id("legacy"), encoding="utf-8")
    (host.state / "container.json").write_text(json.dumps([{
        "Name": "/review-agent-service", "Image": image_id("legacy"),
        "Config": {}, "HostConfig": {"Devices": [{"PathOnHost": "/dev/gpu"}]},
    }]), encoding="utf-8")
    host.release()
    host.run("start-review.sh", success=False)
    assert not (host.runtime / "docker-compose.server.yml").exists()
    assert "load --input" not in (host.state / "commands").read_text()


def test_stop_replace_one_package_start_preserves_scripts_and_config(host):
    host.release("old", "fixed compose")
    host.run("start-review.sh")
    fixed = {name: (host.upload / name).stat().st_mtime_ns for name in (
        "start-review.sh", "stop-review.sh", "rollback-review.sh", "review-common.sh", "docker-compose.server.yml",
    )}
    host.run("stop-review.sh")
    host.release("new", compose=None)
    host.run("start-review.sh")
    assert (host.state / "container").read_text().strip() == image_id("new")
    assert (host.state / "rollback").read_text().strip() == image_id("old")
    assert all((host.upload / name).stat().st_mtime_ns == mtime for name, mtime in fixed.items())
    assert (host.runtime / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"
    assert not list(host.upload.glob(".release-*"))


@pytest.mark.parametrize("name", ["../outside", "start-review.sh"])
def test_unexpected_archive_members_refused_before_image_load(host, name):
    host.release()
    host.members[name] = b"unexpected"
    host.write_archive()
    assert "Unexpected release archive contents" in host.run("start-review.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()


def test_archive_links_refused_before_extraction(host):
    host.release()
    with tarfile.open(host.upload / "forj-review-server.tar.gz", "w:gz") as archive:
        for name in host.members:
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = "../outside"
            archive.addfile(member)
    assert "regular files only" in host.run("start-review.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()


def test_loaded_image_must_match_packaged_metadata(host):
    host.release()
    (host.state / "incoming").write_text(image_id("unexpected"), encoding="utf-8")
    assert "does not match this release" in host.run("start-review.sh", success=False)
    assert " up " not in (host.state / "commands").read_text()


def test_entrypoint_shell_syntax():
    bash = bash_executable()
    if not bash:
        pytest.skip("Bash is unavailable")
    for script in (ROOT / "deploy").glob("*.sh"):
        result = subprocess.run([bash, "-n"], input=script.read_text(encoding="utf-8"), text=True, capture_output=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"
