"""Execute only developer-selected checks inside a disposable, offline container."""

import asyncio
import hashlib
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from forj_review_config_service.contracts.handoff import package_digest
from forj_review_config_service.local_development.workspace import inventory, save_state


class CheckCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=200)
    argv: list[str] = Field(min_length=1, max_length=100)
    timeout: int = Field(default=120, ge=1, le=600)


def validate_checks(value: object) -> list[dict]:
    try:
        checks = TypeAdapter(list[CheckCommand]).validate_python(value, strict=True)
    except ValidationError as exc:
        raise ValueError("checks 每项须包含 id、非空 argv 和 1–600 秒 timeout") from exc
    if (
        not 1 <= len(checks) <= 20
        or len({c.id for c in checks}) != len(checks)
        or any(not c.id.strip() or not c.argv[0].strip() for c in checks)
    ):
        raise ValueError("checks 须为 1–20 项检查，id 唯一且命令非空")
    return [c.model_dump() for c in checks]


async def remove_owned_container(root, state) -> bool:
    """Clear ownership only after Docker confirms removal or absence."""
    name = state.get("active_container")
    if not name:
        return True
    if not name.startswith("forj-local-") or not name[11:].isalnum():
        raise ValueError("容器所有权标识无效")

    async def docker(*args):
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 15)
            return process.returncode, output
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    removed = False
    try:
        code, _ = await docker("rm", "-f", name)
        if code == 0:
            removed = True
        else:
            # Container creation may have failed or an older --rm task already exited.
            code, output = await docker(
                "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"
            )
            removed = code == 0 and not output.strip()
    except (OSError, TimeoutError):
        pass  # Preserve the durable ownership marker for explicit reconciliation.
    if removed:
        state.pop("active_container", None)
    save_state(root, state)
    return removed


async def run_check(root, state, check):
    if state.get("active_container"):
        return {
            "check_id": check["id"],
            "status": "WAITING_ENVIRONMENT",
            "exit_code": None,
            "message": "仍有未核对的任务容器，请先使用 resume --reconcile 清理",
        }
    if not state.get("image_id"):
        inspect = None
        try:
            inspect = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "inspect",
                state["image"],
                "--format",
                "{{.Id}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            image_id, _ = await asyncio.wait_for(inspect.communicate(), 15)
            if inspect.returncode != 0:
                raise OSError("image unavailable")
            state["image_id"] = image_id.decode().strip()
            save_state(root, state)
        except (OSError, TimeoutError):
            result = {
                "check_id": check["id"],
                "status": "WAITING_ENVIRONMENT",
                "exit_code": None,
                "message": "Docker 或已准备的本地依赖镜像不可用；保留补丁后恢复检查",
            }
            return result
        finally:
            if inspect and inspect.returncode is None:
                inspect.kill()
                await inspect.wait()
    name = "forj-local-" + uuid4().hex[:16]
    state["active_container"] = name
    save_state(root, state)
    candidate = package_digest(inventory(root / "work"))
    argv = [
        "docker",
        "run",
        "--name",
        name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=128",
        "--memory=1g",
        "--cpus=2",
        "--user=10001:10001",
        "--log-driver=none",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=256m,mode=1777",
        "--workdir=/work",
        "--mount",
        f"type=bind,source={(root / 'work').resolve()},target=/work,readonly",
        "--env=PYTHONPATH=/work/src:/work",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=HOME=/tmp",
        "--env=PYTEST_ADDOPTS=-p no:cacheprovider",
        "--entrypoint",
        check["argv"][0],
        state["image_id"],
        *check["argv"][1:],
    ]
    started = time.monotonic()
    output = bytearray()
    process = None
    status, exit_code = "WAITING_ENVIRONMENT", None
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        async with asyncio.timeout(check.get("timeout", 120)):
            while chunk := await process.stdout.read(8192):
                if len(output) < 50000:
                    output.extend(chunk[: 50000 - len(output)])
            exit_code = await process.wait()
        status = (
            "PASSED"
            if exit_code == 0
            else "WAITING_ENVIRONMENT"
            if exit_code in {125, 126, 127}
            else "FAILED"
        )
    except (OSError, TimeoutError):
        status = "WAITING_ENVIRONMENT"
    finally:
        # Also covers cancellation: never leave an owned validation container running.
        if not await remove_owned_container(root, state):
            status = "WAITING_ENVIRONMENT"
        if process and process.returncode is None:
            process.kill()
            await process.wait()
    result = {
        "check_id": check["id"],
        "status": status,
        "exit_code": exit_code,
        "candidate_digest": candidate,
        "duration_seconds": round(time.monotonic() - started, 2),
        "output": output.decode("utf8", errors="replace"),
        "output_digest": hashlib.sha256(output).hexdigest(),
    }
    return result
