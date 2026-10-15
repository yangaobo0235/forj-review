"""Check import boundaries without importing the application or loading credentials."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "forj_review_assistant"
SOURCE = ROOT / "src" / PACKAGE


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
            if reason:
                findings.append(f"{relative.as_posix()}:{line}: {reason}: {module}")
    return findings


if __name__ == "__main__":
    findings = check_architecture()
    print("\n".join(findings) if findings else "Python architecture: OK")
    raise SystemExit(bool(findings))
