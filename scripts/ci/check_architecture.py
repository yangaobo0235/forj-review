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
            if parts[0] in {"tests", "scripts", "app"}:
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
    if source == SOURCE:
        findings.extend(check_configuration_service())
        findings.extend(check_database_package())
    return findings


def check_database_package(source: Path | None = None) -> list[str]:
    """Shared persistence cannot depend on either service or repository tooling."""
    source = source or ROOT / "src/forj_review_database"
    findings = []
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        package = ".".join(["forj_review_database", *relative.parts[:-1]])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in runtime_imports(tree, package):
            if module.split(".")[0] in {
                "forj_review_service",
                "forj_review_config_service",
                "tests",
                "scripts",
            }:
                findings.append(
                    f"forj_review_database/{relative.as_posix()}:{line}: "
                    f"shared database imports service or repository tooling: {module}"
                )
    return findings


def check_configuration_service(source: Path | None = None) -> list[str]:
    source = source or ROOT / "src/forj_review_config_service"
    findings = []
    shared = (
        "forj_review_service.contracts.errors.",
        "forj_review_service.businesses.page_config.",
        "forj_review_service.businesses.packs.",
        "forj_review_service.businesses.page_catalog.",
        "forj_review_service.businesses.rules.catalog.",
        "forj_review_service.runtime.configuration.service.",
        "forj_review_service.runtime.configuration.publication.",
        "forj_review_service.runtime.configuration.verification.",
        "forj_review_service.businesses.page_config.state.",
        "forj_review_service.integrations.storage.",
    )
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        group = relative.parts[0] if len(relative.parts) > 1 else "entrypoint"
        package = ".".join(["forj_review_config_service", *relative.parts[:-1]])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, module in runtime_imports(tree, package):
            reason = None
            if module.startswith("forj_review_service"):
                if relative.parts[:2] != ("integrations", "review_kernel") or not module.startswith(
                    shared
                ):
                    reason = (
                        "cross-package access must use the registered review_kernel adapter API"
                    )
            elif module.startswith("forj_review_config_service."):
                target = module.split(".")[1]
                if group in {"contracts", "config"} and target != group:
                    reason = "foundation imports execution"
                elif group in {"application", "workflow", "tools"} and target in {
                    "integrations",
                    "interfaces",
                    "bootstrap",
                }:
                    reason = "orchestration imports a concrete adapter or entrypoint"
                elif group == "integrations" and target in {
                    "application",
                    "workflow",
                    "interfaces",
                    "bootstrap",
                }:
                    reason = "adapter imports orchestration"
            if reason:
                findings.append(
                    f"forj_review_config_service/{relative.as_posix()}:{line}: {reason}: {module}"
                )
    # The review service must never depend on the configuration service.
    if source == ROOT / "src/forj_review_config_service":
        for path in SOURCE.rglob("*.py"):
            if "forj_review_config_service" in path.read_text(encoding="utf-8"):
                findings.append(f"{path.name}: audit package depends on configuration service")
    return findings


if __name__ == "__main__":
    findings = check_architecture()
    print("\n".join(findings) if findings else "Python architecture: OK")
    raise SystemExit(bool(findings))
