"""Copy task inputs without touching the source repository's index or working files."""

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from forj_review_config_service.contracts.handoff import package_digest

EDITABLE = {"src", "apps", "packages", "docs"}
EXCLUDED = {".git", ".env", ".env.database", "node_modules", ".venv", "__pycache__"}


def command(argv, cwd=None):
    return subprocess.check_output(argv, cwd=cwd, stderr=subprocess.PIPE).decode("utf-8")


def safe_path(root: Path, relative: str):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError("文件路径必须位于任务副本")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("文件路径越出任务副本")
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError("任务文件不允许符号链接")
    return target


def allowed(relative):
    parts = PurePosixPath(relative).parts
    return bool(parts) and not any(
        p in EXCLUDED or p.startswith(".env") or p.endswith((".pem", ".key", ".p12")) for p in parts
    )


def iter_files(root: Path) -> Iterator[Path]:
    def on_error(error: OSError) -> None:
        raise error

    resolved = root.resolve()
    if root.is_symlink():
        raise ValueError("任务文件不允许符号链接")
    for directory, directories, filenames in os.walk(root, followlinks=False, onerror=on_error):
        parent = Path(directory)
        if not parent.resolve().is_relative_to(resolved):
            raise ValueError("文件路径越出任务副本")
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in [*directories, *sorted(filenames)]:
            path = parent / name
            if path.is_symlink():
                raise ValueError("任务文件不允许符号链接")
        for name in sorted(filenames):
            if name != ".git":
                yield parent / name


def inventory(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in iter_files(root)
    }


def editable(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return (
        allowed(relative)
        and parts[0] in EDITABLE
        and "tests" not in parts
        and not parts[-1].startswith("test_")
    )


def save_state(root, state):
    temporary = root / "state.pending.json"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(root / "state.json")


def prepare(repo: Path, output: Path, package, checks, image):
    repo, output = repo.resolve(), output.resolve()
    if output.exists():
        raise ValueError("输出目录已存在；恢复请使用 resume")
    if (
        repo == output
        or output.is_relative_to(repo)
        and output.relative_to(repo).parts[0] != ".review-config"
    ):
        raise ValueError("输出目录须位于仓库外或 .review-config 内")
    head = command(["git", "rev-parse", "HEAD"], repo).strip()
    status = command(["git", "status", "--porcelain=v1", "-z"], repo)
    files = command(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], repo
    ).split("\0")
    output.mkdir(parents=True)
    baseline, work = output / "baseline", output / "work"
    baseline.mkdir()
    for relative in sorted(set(files)):
        if not relative or not allowed(relative):
            continue
        source = safe_path(repo, relative)
        if not source.is_file():  # Preserve working deletions in the task baseline.
            continue
        if source.stat().st_size > 5_000_000:
            raise ValueError("任务文件超过 5MB，请拆分工件")
        destination = safe_path(baseline, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    manifest = inventory(baseline)
    if head != command(["git", "rev-parse", "HEAD"], repo).strip() or status != command(
        ["git", "status", "--porcelain=v1", "-z"], repo
    ):
        raise ValueError("复制期间源工作区发生变化，请重新创建任务")
    for relative, digest in manifest.items():
        if hashlib.sha256(safe_path(repo, relative).read_bytes()).hexdigest() != digest:
            raise ValueError("复制期间源文件发生变化，请重新创建任务")
    shutil.copytree(baseline, work)
    command(["git", "init", "-q"], work)
    command(["git", "add", "-f", "."], work)
    command(
        [
            "git",
            "-c",
            "user.name=Local Task",
            "-c",
            "user.email=local@invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Task snapshot",
        ],
        work,
    )
    state = {
        "schema_version": "1.0",
        "package": package.model_dump(),
        "source_head": head,
        "source_status_digest": package_digest(status),
        "baseline": manifest,
        "baseline_digest": package_digest(manifest),
        "checks": checks,
        "image": image,
        "status": "PREPARED",
        "messages": [],
        "evidence": [],
        "calls": 0,
    }
    save_state(output, state)
    return state


def reconcile(root, state):
    if package_digest(inventory(root / "baseline")) != state["baseline_digest"]:
        raise ValueError("冻结基线或验收被改动，停止恢复")
    current = inventory(root / "work")
    changed = {
        p
        for p in current.keys() | state["baseline"].keys()
        if current.get(p) != state["baseline"].get(p)
    }
    if any(not editable(p) for p in changed):
        raise ValueError("候选包含超出允许范围的文件变化，停止执行")
    return sorted(changed)


def export_patch(root, state):
    changes = reconcile(root, state)
    work = root / "work"
    # The index belongs only to the disposable task copy.
    command(["git", "add", "-N", "-f", "."], work)
    patch = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--"], cwd=work)
    (root / "changes.patch").write_bytes(patch)
    return changes, hashlib.sha256(patch).hexdigest()
