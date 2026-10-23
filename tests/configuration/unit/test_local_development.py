"""The local runner preserves user work, freezes checks and exports only task deltas."""

import asyncio
import json
import subprocess

import pytest

from forj_review_config_service.contracts.handoff import HandoffPackage, package_digest
from forj_review_config_service.local_development.tools import LocalToolError, execute_tool
from forj_review_config_service.local_development.workspace import export_patch, prepare, reconcile


def package():
    p = HandoffPackage(
        task_id="task",
        page_id="page",
        intent_revision=0,
        intent_digest="0" * 64,
        candidate_revision=0,
        baseline_digest="0" * 64,
        candidate={},
        requirements=[],
        gaps=[],
        verification={},
        assessment=dict(
            route="LOCAL_CODE",
            reason="新规范化器需要代码",
            evidence_refs=["capability"],
            intent_revision=0,
            revision=0,
            baseline_digest="0" * 64,
            capability_digest="0" * 64,
        ),
    )
    p.digest = package_digest(p.model_dump(exclude={"digest"}))
    return p


def setup(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/code.py").write_bytes(b"value = 1\r\n")
    (repo / "tests/test_existing.py").write_text("assert True", encoding="utf8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "base",
        ],
        cwd=repo,
        check=True,
    )
    (repo / "src/code.py").write_bytes(b"value = 2\r\n")
    subprocess.run(["git", "add", "src/code.py"], cwd=repo, check=True)
    (repo / "src/untracked.py").write_text("user_work = True", encoding="utf8")
    before = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repo)
    root = tmp_path / "task"
    state = prepare(
        repo, root, package(), [{"id": "fixed", "argv": ["python", "--version"]}], "local-image"
    )
    return repo, root, state, before


@pytest.mark.asyncio
async def test_patch_uses_actual_dirty_snapshot_and_preserves_original_index(tmp_path):
    repo, root, state, before = setup(tmp_path)
    content = await execute_tool(root, state, "read_file", {"path": "src/code.py"})
    assert content["content"] == "value = 2\n"
    await execute_tool(
        root,
        state,
        "replace_text",
        {
            "path": "src/code.py",
            "sha256": content["sha256"],
            "old": "value = 2",
            "new": "value = 3",
        },
    )
    changed, _ = export_patch(root, state)
    patch = (root / "changes.patch").read_text(encoding="utf8")
    assert changed == ["src/code.py"] and "-value = 2" in patch and "+value = 3" in patch
    assert "untracked.py" not in patch and "value = 1" not in patch
    assert (repo / "src/code.py").read_bytes() == b"value = 2\r\n"
    assert subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repo) == before


@pytest.mark.asyncio
async def test_stale_file_hash_cannot_overwrite_candidate_and_frozen_tests_cannot_change(tmp_path):
    _repo, root, state, _before = setup(tmp_path)
    with pytest.raises(LocalToolError, match="摘要"):
        await execute_tool(
            root, state, "write_file", {"path": "src/code.py", "sha256": "wrong", "content": "oops"}
        )
    with pytest.raises(ValueError, match="源码"):
        await execute_tool(
            root,
            state,
            "write_file",
            {"path": "tests/test_existing.py", "sha256": "", "content": ""},
        )
    with pytest.raises(ValueError, match="副本"):
        await execute_tool(root, state, "read_file", {"path": "../outside"})
    with pytest.raises(ValueError, match="登记"):
        await execute_tool(root, state, "run_check", {"check_id": "arbitrary-shell"})
    (root / "baseline/src/code.py").write_text("tampered", encoding="utf8")
    with pytest.raises(ValueError, match="冻结"):
        reconcile(root, state)


def test_modified_package_is_not_accepted_as_original_task():
    p = package()
    p.verified()
    p.candidate["other"] = "changed"
    with pytest.raises(ValueError, match="摘要"):
        p.verified()


@pytest.mark.asyncio
async def test_unknown_inflight_stops_before_model_or_replaying_edit(tmp_path):
    from forj_review_config_service.local_development.runner import run

    _repo, root, state, _before = setup(tmp_path)
    state["inflight"] = "unknown-write"
    result = await run(root, state, None)
    assert result["status"] == "RECONCILIATION_REQUIRED"
    assert (
        json.loads((root / "report.json").read_text(encoding="utf8"))["status"] == result["status"]
    )
    assert (root / "work/src/code.py").read_bytes() == b"value = 2\r\n"


@pytest.mark.asyncio
async def test_unfinished_persisted_batch_requires_reconciliation_without_inflight_marker(tmp_path):
    from forj_review_config_service.local_development.runner import run

    _repo, root, state, _before = setup(tmp_path)
    state["messages"] = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "pending-edit", "function": {"name": "write_file", "arguments": "{}"}}
            ],
        }
    ]
    result = await run(root, state, None)
    assert result["status"] == "RECONCILIATION_REQUIRED"
    assert (root / "work/src/code.py").read_bytes() == b"value = 2\r\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check_status, expected",
    [
        ("PASSED", "LOCAL_CHECKS_PASSED"),
        ("WAITING_ENVIRONMENT", "WAITING_ENVIRONMENT"),
        ("FAILED", "UNVERIFIED_PATCH"),
    ],
)
async def test_budget_preserves_patch_and_runs_independent_checks(
    tmp_path, monkeypatch, check_status, expected
):
    from forj_review_config_service.local_development import runner

    repo, root, state, before = setup(tmp_path)
    (root / "work/src/code.py").write_text("value = 3\n", encoding="utf8")
    checks = []

    async def independent_check(task_root, task_state, check):
        checks.append(check["id"])
        return {"status": check_status}

    monkeypatch.setattr(runner, "run_check", independent_check)
    result = await runner.run(root, state, None, max_calls=0)
    assert checks == ["fixed"] and result["status"] == expected
    assert result["stop_reason"] == "BUDGET_EXHAUSTED"
    assert result["changed_files"] == ["src/code.py"]
    report = json.loads((root / "report.json").read_text(encoding="utf8"))
    assert report["task"]["digest"] == state["package"]["digest"]
    assert "真实页面" in report["verification_scope"]
    assert subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repo) == before


@pytest.mark.asyncio
async def test_model_cancellation_keeps_recoverable_state_and_report(tmp_path):
    from forj_review_config_service.local_development.runner import run

    _repo, root, state, _before = setup(tmp_path)

    class CancelledModel:
        async def complete(self, *_):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run(root, state, CancelledModel())
    assert json.loads((root / "state.json").read_text(encoding="utf8"))["status"] == "INTERRUPTED"
    assert json.loads((root / "report.json").read_text(encoding="utf8"))["status"] == "INTERRUPTED"


@pytest.mark.asyncio
async def test_unavailable_docker_is_waiting_environment(tmp_path, monkeypatch):
    from forj_review_config_service.local_development.checks import run_check

    _repo, root, state, _before = setup(tmp_path)

    async def missing_docker(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing_docker)
    result = await run_check(root, state, state["checks"][0])
    assert result["status"] == "WAITING_ENVIRONMENT"
    assert "active_container" not in state


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_code", [0, 1])
async def test_cancelled_check_cleans_only_owned_container_and_retains_failed_cleanup(
    tmp_path, monkeypatch, cleanup_code
):
    from forj_review_config_service.local_development.checks import run_check

    _repo, root, state, _before = setup(tmp_path)
    state["image_id"] = "sha256:fixed-local-image"
    commands = []

    class Process:
        returncode = None

        @property
        def stdout(self):
            return self

        async def read(self, _size):
            raise asyncio.CancelledError

        async def wait(self):
            return self.returncode

        async def communicate(self):
            return b"", b""

        def kill(self):
            self.returncode = -9

    async def start(*argv, **_kwargs):
        commands.append(argv)
        process = Process()
        if argv[1] == "rm":
            process.returncode = cleanup_code
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    with pytest.raises(asyncio.CancelledError):
        await run_check(root, state, state["checks"][0])
    owned_name = commands[0][commands[0].index("--name") + 1]
    assert commands[1] == ("docker", "rm", "-f", owned_name)
    assert owned_name.startswith("forj-local-")
    assert ("active_container" in state) is (cleanup_code != 0)


@pytest.mark.parametrize(
    "checks",
    [
        None,
        [None],
        ["pytest"],
        [{"id": "a", "argv": ["python"], "timeout": True}],
        [{"id": "a", "argv": [""]}],
        [{"id": "a", "argv": ["python"], "typo": 1}],
        [{"id": "a", "argv": ["python"]}, {"id": "a", "argv": ["python"]}],
    ],
)
def test_check_commands_have_one_strict_contract(checks):
    from forj_review_config_service.local_development.checks import validate_checks

    with pytest.raises(ValueError):
        validate_checks(checks)


@pytest.mark.asyncio
async def test_missing_file_cannot_count_as_successful_investigation(tmp_path):
    _repo, root, state, _before = setup(tmp_path)
    with pytest.raises(LocalToolError) as rejected:
        await execute_tool(root, state, "read_file", {"path": "src/nonexistent.py"})
    assert rejected.value.code == "FILE_NOT_FOUND"


def test_reconciliation_also_freezes_frontend_tests(tmp_path):
    repo, root, state, _before = setup(tmp_path)
    path = root / "work/apps/web/tests/existing.test.mjs"
    path.parent.mkdir(parents=True)
    path.write_text("weakened expectation", encoding="utf8")
    with pytest.raises(ValueError, match="允许范围"):
        reconcile(root, state)
    assert not (repo / "apps").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check_status, expected",
    [
        ("PASSED", "LOCAL_CHECKS_PASSED"),
        ("FAILED", "UNVERIFIED_PATCH"),
    ],
)
async def test_current_check_receipt_is_stored_once_and_reused_at_delivery(
    tmp_path,
    monkeypatch,
    check_status,
    expected,
):
    from forj_review_config_service.local_development import runner, tools
    from forj_review_config_service.local_development.workspace import inventory

    _repo, root, state, _before = setup(tmp_path)
    (root / "work/src/code.py").write_text("value = 3\n", encoding="utf8")
    executed = []

    async def check(task_root, _state, command):
        executed.append(command["id"])
        return {
            "check_id": command["id"],
            "status": check_status,
            "candidate_digest": package_digest(inventory(task_root / "work")),
        }

    class Model:
        async def complete(self, *_):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "check",
                        "function": {
                            "name": "run_check",
                            "arguments": '{"check_id":"fixed"}',
                        },
                    }
                ],
            }

    monkeypatch.setattr(tools, "run_check", check)
    monkeypatch.setattr(runner, "run_check", check)
    result = await runner.run(root, state, Model(), max_calls=1)
    assert executed == ["fixed"] and result["status"] == expected
    assert len(state["evidence"]) == 1
    if check_status == "PASSED":
        assert result["delivery"]["evidence_refs"] == [state["evidence"][0]["id"]]


@pytest.mark.asyncio
async def test_passing_checks_cannot_overwrite_explicit_need_for_business_input(tmp_path):
    from forj_review_config_service.local_development.runner import run
    from forj_review_config_service.local_development.workspace import inventory

    _repo, root, state, _before = setup(tmp_path)
    (root / "work/src/code.py").write_text("value = 3\n", encoding="utf8")
    state["evidence"] = [
        {
            "check_id": "fixed",
            "status": "PASSED",
            "candidate_digest": package_digest(inventory(root / "work")),
        }
    ]

    class Model:
        async def complete(self, *_):
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "finish",
                        "function": {
                            "name": "finish",
                            "arguments": json.dumps(
                                {
                                    "status": "NEEDS_INPUT",
                                    "summary": "仍需确认业务口径",
                                    "obstacles": ["未确认日期定义"],
                                    "evidence_refs": [],
                                }
                            ),
                        },
                    }
                ],
            }

    result = await run(root, state, Model(), max_calls=1)
    assert result["status"] == "NEEDS_INPUT"
    assert result["delivery"]["obstacles"] == ["未确认日期定义"]


@pytest.mark.asyncio
async def test_failed_reconciliation_keeps_container_and_unknown_tool(tmp_path, monkeypatch):
    from argparse import Namespace

    from forj_review_config_service.interfaces.develop import execute as develop
    from forj_review_config_service.local_development.workspace import save_state

    _repo, root, state, _before = setup(tmp_path)
    state["active_container"], state["inflight"] = "forj-local-owned", "unknown"
    save_state(root, state)

    async def unavailable(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    with pytest.raises(ValueError, match="尚未清理"):
        await develop(Namespace(output=root, resume=True, reconcile=True))
    stored = json.loads((root / "state.json").read_text(encoding="utf8"))
    assert stored["active_container"] == "forj-local-owned" and stored["inflight"] == "unknown"


@pytest.mark.asyncio
async def test_human_handoff_needs_distinct_successful_investigation_not_duplicate_refs(tmp_path):
    _repo, root, state, _before = setup(tmp_path)
    first = await execute_tool(root, state, "read_file", {"path": "src/code.py"})
    state["evidence"].append({"id": "read-source", "tool": "read_file", "result": first})
    args = {
        "status": "HUMAN_HANDOFF",
        "summary": "需要人工决定现有工程以外的契约",
        "obstacles": ["验收依赖尚不存在的业务系统协议"],
        "evidence_refs": ["read-source", "read-source"],
    }
    with pytest.raises(ValueError, match="不同"):
        await execute_tool(root, state, "finish", args)
    state["evidence"].append({"id": "duplicate-read", "tool": "read_file", "result": first})
    args["evidence_refs"] = ["read-source", "duplicate-read"]
    with pytest.raises(ValueError, match="不同"):
        await execute_tool(root, state, "finish", args)
    second = await execute_tool(root, state, "search", {"text": "protocol"})
    state["evidence"].append({"id": "search-contract", "tool": "search", "result": second})
    args["evidence_refs"] = ["read-source", "search-contract"]
    assert (await execute_tool(root, state, "finish", args))["finish"]["status"] == "HUMAN_HANDOFF"
