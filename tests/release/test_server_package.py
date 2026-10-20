"""Exercise daily packaging without changing the fixed server installation files."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def release_project(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell or not shutil.which("tar"):
        pytest.skip("Packaging requires PowerShell and tar")
    project = tmp_path / "project"
    scripts = project / "scripts/release"
    scripts.mkdir(parents=True)
    for name in ("package-server.ps1", "release-common.ps1"):
        shutil.copyfile(ROOT / "scripts/release" / name, scripts / name)
    wrapper = project / "invoke.ps1"
    wrapper.write_text("""
function git {
    $global:LASTEXITCODE = 0
    if ($args -contains 'rev-parse') { 'abcdef123456' }
}
function docker {
    $global:LASTEXITCODE = 0
    switch ($args[0]) {
        'build' { if ($env:FAIL_BUILD) { $global:LASTEXITCODE = 1 } }
        'image' { 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        'save' { [IO.File]::WriteAllText($args[2], 'synthetic image') }
    }
}
& "$PSScriptRoot/scripts/release/$($env:RELEASE_ENTRYPOINT).ps1"
""", encoding="utf-8")

    def run(entrypoint, **env):
        return subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
                              env={**os.environ, "RELEASE_ENTRYPOINT": entrypoint, **env}, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)

    return project, run


def test_daily_package_only_writes_archive_and_preserves_fixed_tools(release_project):
    project, run = release_project
    tools = project / "deploy/server"
    tools.mkdir(parents=True)
    for name in (".env", ".env.database", "start-server.sh", "docker-compose.server.yml"):
        (tools / name).write_text("operator-owned " + name, encoding="utf-8")
    original_tools = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tools.iterdir()}
    # Fixed files model the operator's installation sources, outside release/.
    result = run("package-server")
    assert result.returncode == 0, result.stdout + result.stderr
    package_path = project / "release/server/forj-review-server.tar.gz"
    with tarfile.open(package_path) as archive:
        assert archive.getnames() == ["forj-review-service-main.tar", "release.json", "SHA256SUMS"]
        files = {member.name: archive.extractfile(member).read() for member in archive}
    assert files["forj-review-service-main.tar"] == b"synthetic image"
    assert json.loads(files["release.json"])["image_id"] == "sha256:" + "a" * 64
    for line in files["SHA256SUMS"].decode().splitlines():
        checksum, name = line.split("  ")
        assert hashlib.sha256(files[name]).hexdigest() == checksum
    assert {path.name for path in package_path.parent.iterdir()} == {"forj-review-server.tar.gz"}
    assert not (project / "deploy/forj-review-server.tar.gz").exists()
    assert not list((project / ".tmp").glob("server-stage-*"))
    assert not (project / ".tmp/server-release").exists()
    assert not (project / ".tmp/server-deploy").exists()
    result = run("package-server")
    assert result.returncode == 0, result.stdout + result.stderr
    previous = package_path.read_bytes()
    assert run("package-server", FAIL_BUILD="1").returncode != 0
    assert package_path.read_bytes() == previous
    assert {path.name for path in package_path.parent.iterdir()} == {"forj-review-server.tar.gz"}
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tools.iterdir()} == original_tools
