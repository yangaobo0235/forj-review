"""从当前源码导出业务复现资料；只读声明，不调用模型或修改业务配置。

在仓库根目录执行：
uv run python scripts/dev/export-business-snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from forj_review_assistant.businesses.configuration.schema import PageDefinition  # noqa: E402
from forj_review_assistant.businesses.packs.model import build_collect_manifest  # noqa: E402
from forj_review_assistant.businesses.page_catalog import page_catalog  # noqa: E402
from forj_review_assistant.businesses.registry import build_business_registry  # noqa: E402
from forj_review_assistant.contracts.recognition import (  # noqa: E402
    AgentBatchResult,
    QwenClassification,
    QwenExtraction,
)
from forj_review_assistant.contracts.review_schema import review_contract_schema  # noqa: E402


def main() -> None:
    registry = build_business_registry()
    profiles = []
    for profile in registry.profiles:
        item = {
            "business_type": str(profile.business_type),
            "region": str(profile.region),
            "version": profile.version,
            "page_id": profile.page_id,
            "active": any(profile is active for active in registry.active_profiles),
            "configured": profile.rules_configured,
            "unconfigured_message": profile.unconfigured_message,
            "page_interaction": profile.page_interaction,
            "required_fields": list(profile.required_fields),
            "capabilities": [asdict(spec) for spec in profile.capabilities],
            "bindings": [asdict(binding) for binding in profile.bindings],
            "material_policy": asdict(profile.material_policy) if profile.material_policy else None,
            "field_check_bindings": list(profile.field_check_bindings),
            "page_actions": list(profile.enabled_page_actions),
            "retry_policy": asdict(profile.retry_policy),
        }
        if profile.rules_configured and profile.pack_declaration:
            item.update(
                manifest=build_collect_manifest(profile.pack_declaration),
                pack=asdict(profile.pack_declaration),
            )
        profiles.append(item)
    source_files = []
    for directory, suffixes in (
        (ROOT / "src/forj_review_assistant", {".py"}),
        (ROOT / "src/forj_configuration_agent", {".py"}),
        (ROOT / "apps/extension/src", {".ts", ".vue", ".css"}),
        (ROOT / "apps/configuration-web/src", {".ts", ".vue", ".css"}),
        (ROOT / "packages/configuration-contracts", {".ts"}),
    ):
        source_files.extend(
            path for path in directory.rglob("*") if path.is_file() and path.suffix in suffixes
        )
    for relative in (
        "docker-compose.server.yml",
        "scripts/dev/start-agent.ps1",
        "apps/extension/package.json",
        "pyproject.toml",
        "uv.lock",
        "apps/extension/package-lock.json",
        "apps/extension/vite.config.ts",
        "apps/extension/public/manifest.json",
        "apps/configuration-web/package.json",
        "apps/configuration-web/package-lock.json",
        "apps/configuration-web/vite.config.ts",
        "scripts/dev/export-business-snapshot.py",
    ):
        path = ROOT / relative
        if path.is_file():
            source_files.append(path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    payload = {
        "snapshot_version": "1.0",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_head": commit,
        "baseline": "当前工作区源码，包含未提交改动；不能只按 git_head 还原",
        "source_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source_files)
        },
        "catalog": page_catalog(registry.active_profiles),
        "profiles": profiles,
        "review_contract_schema": review_contract_schema(),
        "page_configuration_schema": PageDefinition.model_json_schema(),
        "reproduction_schemas": {
            "QwenClassification": QwenClassification.model_json_schema(),
            "QwenExtraction": QwenExtraction.model_json_schema(),
            "AgentBatchResult": AgentBatchResult.model_json_schema(),
        },
    }
    output = ROOT / "docs/参考/业务流程配置快照.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(profiles)} profiles; {len(source_files)} source hashes; {output}")


if __name__ == "__main__":
    main()
