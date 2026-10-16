"""Exercise the actual PowerShell packager with a synthetic Docker image."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_single_backend_package_and_independent_server_tools(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell or not shutil.which("tar"):
        pytest.skip("Packaging requires PowerShell and tar")
    project = tmp_path / "project"
    scripts = project / "scripts/release"
    scripts.mkdir(parents=True)
    shutil.copytree(ROOT / "deploy", project / "deploy")
    shutil.copyfile(ROOT / "docker-compose.server.yml", project / "docker-compose.server.yml")
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
& "$PSScriptRoot/scripts/release/package-server.ps1"
""", encoding="utf-8")

    def package(**env):
        return subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
                              env={**os.environ, **env}, capture_output=True, text=True, timeout=30)

    result = package()
    assert result.returncode == 0, result.stdout + result.stderr
    package_path = project / ".tmp/server-release/forj-review-server.tar.gz"
    with tarfile.open(package_path) as archive:
        assert archive.getnames() == ["forj-review-api-main.tar", "release.json", "SHA256SUMS"]
        files = {member.name: archive.extractfile(member).read() for member in archive}
    assert files["forj-review-api-main.tar"] == b"synthetic image"
    assert json.loads(files["release.json"])["image_id"] == "sha256:" + "a" * 64
    for line in files["SHA256SUMS"].decode().splitlines():
        checksum, name = line.split("  ")
        assert hashlib.sha256(files[name]).hexdigest() == checksum
    tools = project / ".tmp/server-deploy"
    assert {path.name for path in tools.iterdir()} == {
        "start-review.sh", "stop-review.sh", "rollback-review.sh", "review-common.sh",
        "docker-compose.server.yml", "DEPLOY.md",
        "recover-legacy-compose.py",
    }
    for script in tools.glob("*.sh"):
        assert b"\r" not in script.read_bytes() and not script.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not list((project / ".tmp").glob("server-stage-*"))
    previous = package_path.read_bytes()
    assert package(FAIL_BUILD="1").returncode != 0
    assert package_path.read_bytes() == previous
