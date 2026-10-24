"""Check import boundaries without importing the application or loading credentials."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "forj_review_service"
SOURCE = ROOT / "src" / PACKAGE
GENERIC_WORKFLOW_MODULES = {
    "runtime/workflow/graph.py",
    "runtime/workflow/capability_execution.py",
    "runtime/workflow/planner.py",
    "runtime/workflow/state.py",
}


def runtime_imports(node: ast.AST, package: str):
    if isinstance(node, ast.If) and ast.unparse(node.test) in {
        "TYPE_CHECKING",
        "typing.TYPE_CHECKING",
    }:
        for child in node.orelse:
            yield from runtime_imports(child, package)
        return
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield node.lineno, alias.name
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if node.level:
            base = package.split(".")[: len(package.split(".")) - node.level + 1]
            module = ".".join([*base, *([module] if module else [])])
        for alias in node.names:
            yield node.lineno, module if alias.name == "*" else f"{module}.{alias.name}"
    for child in ast.iter_child_nodes(node):
        yield from runtime_imports(child, package)


def check_architecture(source: Path = SOURCE) -> list[str]:
    findings = []
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        group = relative.parts[0] if len(relative.parts) > 1 else "entrypoint"
        package = ".".join([PACKAGE, *relative.parts[:-1]])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, module in runtime_imports(tree, package):
            parts = module.split(".")
            if parts[0] == PACKAGE and parts[-1].startswith("_"):
                findings.append(f"{relative.as_posix()}:{line}: imports private API: {module}")
            reason = None
            if parts[0] in {
                "tests",
                "scripts",
                "app",
                "psycopg",
                "sqlalchemy",
                "sqlite3",
                "forj_review_database",
                "forj_review_config_service",
            }:
                reason = "product code imports repository tooling or a retired package"
            elif parts[0] == PACKAGE and len(parts) > 1:
                target = parts[1]
                if group in {"contracts", "config"} and target != group:
                    reason = "foundation module imports an execution module"
                elif group not in {"entrypoint", "bootstrap", "interfaces"} and target in {
                    "bootstrap",
                    "interfaces",
                    "main",
                }:
                    reason = "execution module imports an entrypoint or composition root"
                elif group in {"businesses", "evidence", "capabilities"} and target in {
                    "runtime",
                    "integrations",
                }:
                    reason = "domain module imports runtime or a concrete external client"
                elif group == "integrations" and target in {"runtime", "presentation"}:
                    reason = "external adapter imports orchestration or presentation"
                elif group == "evidence" and target == "presentation":
                    reason = "evidence decisions import API response rendering"
            if reason:
                findings.append(f"{relative.as_posix()}:{line}: {reason}: {module}")
            if (
                relative.as_posix() in GENERIC_WORKFLOW_MODULES
                and module.startswith(f"{PACKAGE}.businesses.rules.")
                and not module.startswith(f"{PACKAGE}.businesses.rules.catalog.")
            ):
                findings.append(
                    f"{relative.as_posix()}:{line}: workflow imports a concrete business rule: {module}"
                )
            if relative.as_posix() in {
                "runtime/workflow/capability_execution.py",
                "runtime/workflow/state.py",
            } and module.startswith(
                (
                    f"{PACKAGE}.runtime.workflow.graph.",
                    f"{PACKAGE}.presentation.",
                )
            ):
                findings.append(
                    f"{relative.as_posix()}:{line}: workflow state/execution imports graph or presentation: {module}"
                )
            if (
                relative.as_posix() == "presentation/task_factory.py"
                and module.startswith(f"{PACKAGE}.")
                and not module.startswith(f"{PACKAGE}.contracts.")
            ):
                findings.append(
                    f"{relative.as_posix()}:{line}: task factory imports execution or business policy: {module}"
                )
            if relative.as_posix() == "runtime/jobs/progress.py" and module.startswith(
                f"{PACKAGE}.runtime.jobs.manager."
            ):
                findings.append(
                    f"{relative.as_posix()}:{line}: progress view imports job lifecycle manager: {module}"
                )
            if relative.parts[:2] == ("businesses", "page_config") and module.startswith(
                (
                    f"{PACKAGE}.runtime",
                    f"{PACKAGE}.integrations",
                    f"{PACKAGE}.interfaces",
                    f"{PACKAGE}.bootstrap",
                )
            ):
                findings.append(
                    f"{relative.as_posix()}:{line}: configuration compiler imports an execution adapter: {module}"
                )
        if relative.parts[:2] == ("businesses", "page_config"):
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id
                    in {
                        "eval",
                        "exec",
                        "__import__",
                    }
                ):
                    findings.append(
                        f"{relative.as_posix()}:{node.lineno}: configuration executes dynamic code"
                    )
        if relative.as_posix() in GENERIC_WORKFLOW_MODULES:
            business_ids = {
                "scrap_certificate_qr",
                "verify_invoice",
                "old_vehicle.affiliation",
                "new_vehicle.affiliation",
                "scrap_replacement",
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in business_ids
                ):
                    findings.append(
                        f"{relative.as_posix()}:{node.lineno}: workflow embeds business registration: {node.value}"
                    )
    return findings


if __name__ == "__main__":
    findings = check_architecture()
    print("\n".join(findings) if findings else "Python architecture: OK")
    raise SystemExit(bool(findings))
