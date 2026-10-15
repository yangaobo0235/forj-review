"""从当前源码导出业务复现资料；只读声明，不调用模型或修改业务配置。

在仓库根目录执行：
review-agent-service/.venv/Scripts/python.exe docs/参考/导出业务流程配置快照.py
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
sys.path.insert(0, str(ROOT / "review-agent-service"))

from app.businesses.page_catalog import page_catalog  # noqa: E402
from app.businesses.packs.model import build_collect_manifest  # noqa: E402
from app.businesses.profiles import BUSINESS_PROFILES  # noqa: E402
from app.contracts.review_schema import review_contract_schema  # noqa: E402
from app.workflow.models import AgentBatchResult, QwenClassification, QwenExtraction  # noqa: E402


def main() -> None:
    profiles = []
    for profile in BUSINESS_PROFILES:
        item = {
            "business_type": profile.business_type.value,
            "region": profile.region.value,
            "version": profile.version,
            "page_id": profile.page_id,
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
        (ROOT / "review-agent-service/app", {".py"}),
        (ROOT / "review-extension/src", {".ts", ".tsx", ".css"}),
    ):
        source_files.extend(path for path in directory.rglob("*") if path.is_file() and path.suffix in suffixes)
    for relative in ("docker-compose.server.yml", "start-agent.ps1", "review-extension/package.json",
                     "review-agent-service/pyproject.toml", "review-agent-service/uv.lock",
                     "review-extension/package-lock.json", "review-extension/vite.config.ts"):
        path = ROOT / relative
        if path.is_file():
            source_files.append(path)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    payload = {
        "snapshot_version": "1.0",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_head": commit,
        "baseline": "当前工作区源码，包含未提交改动；不能只按 git_head 还原",
        "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(source_files)},
        "catalog": page_catalog(),
        "profiles": profiles,
        "review_contract_schema": review_contract_schema(),
        "reproduction_schemas": {
            "QwenClassification": QwenClassification.model_json_schema(),
            "QwenExtraction": QwenExtraction.model_json_schema(),
            "AgentBatchResult": AgentBatchResult.model_json_schema(),
        },
    }
    output = Path(__file__).with_name("业务流程配置快照.json")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Exported {len(profiles)} profiles; {len(source_files)} source hashes; {output}")


if __name__ == "__main__":
    main()
