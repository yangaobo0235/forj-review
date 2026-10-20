"""Exercise real Bash entrypoints against a simulated Docker host, never the cloud."""

import hashlib
import io
import json
import os
import shutil
import subprocess
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
if [[ $1 == info || $1 == network ]]; then exit 0; fi
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
    forj-review-service:main) cat "$FAKE_STATE/main";;
    *) exit 1;;
  esac
  exit $?
fi
if [[ $1 == run ]]; then
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
    (upload / ".env").write_text("DASHSCOPE_API_KEY=synthetic\n", encoding="utf-8")
    for name in ("setup-server.sh", "start-server.sh", "stop-server.sh", "rollback-server.sh", "server-common.sh"):
        shutil.copyfile(ROOT / "deploy/server" / name, upload / name)
    (upload / ".env.database").write_text("PGPASSWORD=synthetic\n", encoding="utf-8")
    for name in ("review.env.example", "database.env.example"):
        shutil.copyfile(ROOT / "deploy/server" / name, upload / name)
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
            self.members = {"forj-review-service-main.tar": ("synthetic " + image).encode(),
                            "release.json": (json.dumps({"image_id": image_id(image)}, indent=2) + "\n").encode()}
            self.members["SHA256SUMS"] = "".join(
                f"{hashlib.sha256(content).hexdigest()}  {name}\n"
                for name, content in self.members.items()
            ).encode()
            self.write_archive()

        def run(self, name, *, success=True, default_runtime=False):
            env = {**os.environ, "REVIEW_DEPLOY_DIR": bash_path(upload), "REVIEW_RUNTIME_DIR": bash_path(runtime),
                   "FAKE_STATE": bash_path(state), "TEST_BIN": bash_path(commands)}
            if default_runtime:
                env.pop("REVIEW_RUNTIME_DIR", None)
            result = subprocess.run([bash, "-c", 'if command -v cygpath >/dev/null; then TEST_BIN=$(cygpath -u "$TEST_BIN"); fi; export PATH="$TEST_BIN:$PATH"; exec bash "$REVIEW_DEPLOY_DIR/$1"', "test", name],
                                    env=env, capture_output=True, text=True, timeout=20)
            assert (result.returncode == 0) == success, result.stdout + result.stderr
            return result.stdout + result.stderr

    item = Host()
    item.upload, item.runtime, item.state = upload, runtime, state
    return item


def test_upgrade_repeat_failure_retry_and_rollback_keep_the_healthy_pair(host):
    host.release("old", "old config")
    host.run("start-server.sh")
    assert not (host.state / "rollback").exists()
    host.release("new", "new config")
    host.run("start-server.sh")
    assert (host.state / "rollback").read_text().strip() == image_id("old")
    assert (host.runtime / "rollback-compose.yml").read_text() == "old config"
    host.run("start-server.sh")
    assert (host.state / "rollback").read_text().strip() == image_id("old")
    host.release("broken", "broken config")
    (host.state / "unhealthy").touch()
    host.run("start-server.sh", success=False)
    host.run("start-server.sh", success=False)
    assert (host.state / "rollback").read_text().strip() == image_id("new")
    assert (host.runtime / "rollback-compose.yml").read_text() == "new config"
    (host.state / "unhealthy").unlink()
    host.run("rollback-server.sh")
    assert (host.state / "container").read_text().strip() == image_id("new")
    assert (host.runtime / "docker-compose.server.yml").read_text() == "new config"
    assert (host.runtime / ".last-successful-image").read_text().strip() == image_id("new")


def test_checksum_failure_prevents_image_load_or_container_changes(host):
    host.release()
    host.members["forj-review-service-main.tar"] = b"corrupt"
    host.write_archive()
    assert "checksum verification failed" in host.run("start-server.sh", success=False)
    commands = (host.state / "commands").read_text()
    assert "load --input" not in commands and " up " not in commands


def test_existing_container_and_stop_preserve_data(host):
    (host.state / "container").write_text(image_id("legacy"), encoding="utf-8")
    (host.runtime / "docker-compose.server.yml").write_text("legacy config", encoding="utf-8")
    host.release()
    host.run("start-server.sh")
    assert (host.state / "rollback").read_text().strip() == image_id("legacy")
    host.run("stop-server.sh")
    commands = (host.state / "commands").read_text()
    assert "stop review-service" in commands
    assert " down" not in commands and " rmi" not in commands and "prune" not in commands
    assert (host.upload / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"


def test_missing_previous_compose_without_container_refuses_incomplete_rollback(host):
    (host.state / "main").write_text(image_id("legacy"), encoding="utf-8")
    host.release()
    assert "Compose file is missing" in host.run("start-server.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()






def test_stop_replace_one_package_start_preserves_scripts_and_config(host):
    host.release("old", "fixed compose")
    host.run("start-server.sh")
    fixed = {name: (host.upload / name).stat().st_mtime_ns for name in (
        "start-server.sh", "stop-server.sh", "rollback-server.sh", "server-common.sh", "docker-compose.server.yml",
    )}
    host.run("stop-server.sh")
    host.release("new", compose=None)
    host.run("start-server.sh")
    assert (host.state / "container").read_text().strip() == image_id("new")
    assert (host.state / "rollback").read_text().strip() == image_id("old")
    assert all((host.upload / name).stat().st_mtime_ns == mtime for name, mtime in fixed.items())
    assert (host.upload / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"
    assert not list(host.upload.glob(".release-*"))


@pytest.mark.parametrize("name", ["../outside", "start-server.sh"])
def test_unexpected_archive_members_refused_before_image_load(host, name):
    host.release()
    host.members[name] = b"unexpected"
    host.write_archive()
    assert "Unexpected release archive contents" in host.run("start-server.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()


def test_archive_links_refused_before_extraction(host):
    host.release()
    with tarfile.open(host.upload / "forj-review-server.tar.gz", "w:gz") as archive:
        for name in host.members:
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = "../outside"
            archive.addfile(member)
    assert "regular files only" in host.run("start-server.sh", success=False)
    assert "load --input" not in (host.state / "commands").read_text()


def test_loaded_image_must_match_packaged_metadata(host):
    host.release()
    (host.state / "incoming").write_text(image_id("unexpected"), encoding="utf-8")
    assert "does not match this release" in host.run("start-server.sh", success=False)
    assert " up " not in (host.state / "commands").read_text()


def test_entrypoint_shell_syntax():
    bash = bash_executable()
    if not bash:
        pytest.skip("Bash is unavailable")
    for script in (ROOT / "deploy/server").glob("*.sh"):
        assert b"\r" not in script.read_bytes() and not script.read_bytes().startswith(b"\xef\xbb\xbf")
        result = subprocess.run([bash, "-n"], input=script.read_text(encoding="utf-8"), text=True, capture_output=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_setup_is_repeatable_and_preserves_existing_credentials(host):
    host.run("setup-server.sh")
    host.run("setup-server.sh")
    assert (host.upload / ".runtime").is_dir()
    assert (host.upload / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"
    assert (host.upload / ".env.database").read_text() == "PGPASSWORD=synthetic\n"
    commands = (host.state / "commands").read_text()
    assert " up " not in commands and "prune" not in commands and "volume" not in commands


def test_setup_creates_only_missing_configuration_files(host):
    (host.upload / ".env.database").unlink()
    host.run("setup-server.sh")
    assert (host.upload / ".env.database").read_bytes() == (ROOT / "deploy/server/database.env.example").read_bytes()
    assert (host.upload / ".env").read_text() == "DASHSCOPE_API_KEY=synthetic\n"


def test_first_start_uses_the_single_directory_layout(host):
    host.run("setup-server.sh")
    host.release()
    host.run("start-server.sh", default_runtime=True)
    runtime = host.upload / ".runtime"
    assert (runtime / "docker-compose.server.yml").read_text() == "new config"
    assert (runtime / ".last-successful-image").read_text().strip() == image_id("new")
    assert not (runtime / ".env").exists()
    assert not (runtime / ".env.database").exists()
