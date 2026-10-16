"""Validate a recovered runtime using the real Compose parser, without Docker jobs."""

import json
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECOVERY = runpy.run_path(str(ROOT / "deploy/recover-legacy-compose.py"))


def test_actual_compose_parser_preserves_runtime_environment_and_resources(tmp_path):
    docker = shutil.which("docker")
    if not docker or subprocess.run([docker, "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose CLI is unavailable (daemon is not required)")
    snapshot = {
        "Name": "/review-agent-service", "Image": "sha256:" + "a" * 64,
        "Config": {"Env": ["DASHSCOPE_API_KEY=synthetic$value"], "Entrypoint": None,
                   "Cmd": ["python", "-m", "old_module"], "User": "10001",
                   "WorkingDir": "/app", "Hostname": "old-host", "Domainname": "",
                   "Healthcheck": {"Test": ["CMD", "python", "-V"], "Interval": 30000000000,
                                   "Timeout": 10000000000, "Retries": 3}},
        "HostConfig": {
            "NetworkMode": "review-assistant_default", "ReadonlyRootfs": True,
            "RestartPolicy": {"Name": "unless-stopped"}, "Memory": 1073741824,
            "MemorySwap": 2147483648, "NanoCpus": 2000000000, "IpcMode": "private",
            "CgroupnsMode": "private", "ShmSize": 67108864, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"], "Tmpfs": {"/tmp": "size=256m,mode=1777"},
            "LogConfig": {"Type": "json-file", "Config": {}},
            "PortBindings": {"8010/tcp": [{"HostIp": "", "HostPort": "18110"}]},
        },
        "Mounts": [{"Type": "bind", "Source": "/legacy/logs", "Destination": "/logs",
                    "RW": True, "Propagation": "rprivate"},
                   {"Type": "volume", "Name": "original-volume", "Destination": "/state", "RW": True}],
        "NetworkSettings": {"Networks": {"review-assistant_default": {"Aliases": ["review-agent"]}}},
    }
    document = RECOVERY["escape_interpolation"](RECOVERY["recover"](snapshot))
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run([docker, "compose", "-f", str(path), "config", "--format", "json"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    service = rendered["services"]["review-agent"]
    # `config` re-escapes literal dollars in its reusable normalized output.
    # Without escaping the input, Compose would expand/remove `$value` instead.
    assert service["environment"]["DASHSCOPE_API_KEY"] == "synthetic$$value"
    assert "variable is not set" not in result.stderr
    assert service["image"] == snapshot["Image"]
    assert int(service["mem_limit"]) == snapshot["HostConfig"]["Memory"]
    assert float(service["cpus"]) == 2
    assert rendered["volumes"]["legacy-volume-1"]["name"] == "original-volume"
    assert rendered["networks"]["legacy-network-0"]["name"] == "review-assistant_default"
