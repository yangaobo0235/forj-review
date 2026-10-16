"""The only adapter importing the existing review/configuration library."""

import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from forj_configuration_agent.contracts.models import AgentError, SessionRecord
from forj_review_assistant.businesses.configuration.history import record_snapshot
from forj_review_assistant.businesses.configuration.inspection import (
    compare_configurations,
    digest,
    effective_configuration,
    profile_fingerprint,
)
from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.packs import build_collect_manifest
from forj_review_assistant.businesses.page_catalog import page_catalog
from forj_review_assistant.businesses.rules.catalog import RULE_DEFINITIONS
from forj_review_assistant.config.configuration_io import (
    KERNEL_VERSION,
    PENDING_COMMIT,
    configuration_lock,
)
from forj_review_assistant.runtime.configuration.service import (
    ReplaySuite,
    decision_policy,
    inspect_directory,
    replay_cases,
    validated_configuration,
)


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ReviewKernel:
    def __init__(self, pages_dir: Path, cases_dir: Path, review_url: str, local_cases_dir: Path | None = None):
        self.directory, self.cases_dir, self.review_url = pages_dir, cases_dir, review_url
        self.local_cases_dir = local_cases_dir or cases_dir
        pages_dir.mkdir(parents=True, exist_ok=True)

    def baseline(self) -> str:
        with configuration_lock(self.directory):
            return digest({"kernel": KERNEL_VERSION, "snapshot": inspect_directory(self.directory),
                           "files": self._files(self.directory)})

    @staticmethod
    def _files(root: Path) -> dict[str, str]:
        return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted([*root.glob("*.json"), *root.glob(".history/*.json")])}

    def context(self, page_id: str | None = None) -> dict:
        with configuration_lock(self.directory):
            pages, registry = validated_configuration(self.directory)
            profiles = registry.active_profiles
            if not page_id:
                return {"templates": [{"page_id": p.page_id, "version": p.version,
                         "business_type": str(p.business_type), "region": str(p.region),
                         "versions": [item.version for item in registry.profiles if item.page_id == p.page_id],
                         "title": p.pack_declaration.title if p.pack_declaration else p.page_id}
                        for p in profiles if p.pack_declaration], "case_sets": self.case_sets()}
            profile = next((p for p in profiles if p.page_id == page_id), None)
            if profile is None:
                raise AgentError("NOT_FOUND", "指定页面不存在", 404)
            if not profile.pack_declaration:
                raise AgentError("UNSUPPORTED_CAPABILITY", "此旧业务尚无声明式配置，请通过新页面配置接入", 422)
            source = next((d.model_dump(mode="json", exclude_unset=True)
                           for _, d in pages.sources if d.page_id == page_id
                           and d.version == profile.version), None)
            return {"source": source, "effective": effective_configuration(profile),
                    "reference": {"page_id": profile.page_id, "version": profile.version},
                    "manifest": build_collect_manifest(profile.pack_declaration)}

    def capabilities(self) -> dict:
        return {"schema": PageDefinition.model_json_schema(), "kernel_version": KERNEL_VERSION,
                "decision_policy": decision_policy(),
                "rules": {key: {"fields": sorted(value.fields), "checks": list(value.checks)}
                          for key, value in RULE_DEFINITIONS.items()}}

    def rollback_draft(self, page_id: str, version: str) -> dict:
        with configuration_lock(self.directory):
            pages, registry = validated_configuration(self.directory)
            target = next((p for p in registry.profiles if p.page_id == page_id and p.version == version), None)
            if not target:
                raise AgentError("NOT_FOUND", "历史版本不存在", 404)
            definition = next((d for _, d in pages.sources if d.page_id == page_id and d.version == version), None)
            if definition:
                return {**definition.model_dump(mode="json", exclude_unset=True), "active": True}
            return {"page_id": page_id, "version": version, "business_type": str(target.business_type),
                    "region": str(target.region), "title": target.pack_declaration.title,
                    "paths": ["/"], "active": True}

    def template_members(self, reference: dict | None) -> dict:
        if not reference:
            return {}
        with configuration_lock(self.directory):
            _, registry = validated_configuration(self.directory)
            target = next((p for p in registry.profiles if p.page_id == reference.get("page_id")
                           and p.version == reference.get("version")), None)
            if not target or not target.pack_declaration:
                return {}
            return {"fields": [f.key for f in target.pack_declaration.fields],
                    "materials": [m.document_type for m in target.pack_declaration.materials]}

    def case_sets(self):
        return list(self._case_registry())

    def _case_registry(self):
        registry = {p.relative_to(self.cases_dir).with_suffix("").as_posix(): p
                    for p in sorted(self.cases_dir.rglob("*.json"))
                    if '"cases"' in p.read_text(encoding="utf-8-sig")}
        if self.local_cases_dir != self.cases_dir:
            registry.update({"local/" + p.stem: p for p in sorted(self.local_cases_dir.glob("*.json"))})
        return registry

    def _case_path(self, case_set_id: str) -> Path:
        if case_set_id not in self.case_sets():
            raise AgentError("NOT_FOUND", "回放集不存在；只能选择已登记的独立验收样例", 404)
        return self._case_registry()[case_set_id]

    def register_cases(self, case_set_id: str, suite: dict):
        # This entry is for human-supplied test expectations, never a model tool.
        if not case_set_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in case_set_id):
            raise AgentError("INVALID_CASE_ID", "样例 ID 只允许小写字母、数字、下划线和短横线", 422)
        parsed = ReplaySuite.model_validate(suite)
        target = self.local_cases_dir / f"{case_set_id}.json"
        if target.exists():
            raise AgentError("CASE_IMMUTABLE", "已登记基准不能覆盖，请登记新的样例集")
        atomic_write(target, parsed.model_dump_json(indent=2))
        return {"case_set_id": ("local/" if self.local_cases_dir != self.cases_dir else "") + case_set_id}

    def stage(self, record: SessionRecord, destination: Path):
        definition = PageDefinition.model_validate(record.draft)
        if not definition.active:
            raise ValueError("候选必须明确启用目标新版本")
        shutil.copytree(self.directory, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".transactions", PENDING_COMMIT, "*.lock"))
        pages, registry = validated_configuration(destination)
        if record.activation_only:
            if self.rollback_draft(definition.page_id, definition.version) != record.draft:
                raise ValueError("回退目标内容与历史版本不一致")
            for filename, existing in pages.sources:
                if existing.page_id == definition.page_id:
                    payload = existing.model_dump(mode="json", exclude_unset=True)
                    payload["active"] = existing.version == definition.version
                    (destination / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return definition
        if any(p.page_id == definition.page_id and p.version == definition.version for p in registry.profiles):
            raise ValueError("新配置必须使用未存在的新版本号")
        for filename, existing in pages.sources:
            if existing.page_id == definition.page_id and existing.active:
                payload = existing.model_dump(mode="json", exclude_unset=True)
                payload["active"] = False
                (destination / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        name = digest([definition.page_id, definition.version]) + ".json"
        (destination / name).write_text(json.dumps(record.draft, ensure_ascii=False, indent=2), encoding="utf-8")
        return definition

    def validate(self, record: SessionRecord) -> dict:
        with configuration_lock(self.directory), TemporaryDirectory(prefix="forj-candidate-") as tmp:
            if self.baseline() != record.baseline_digest:
                raise AgentError("BASELINE_CHANGED", "配置或模板已改变，请重新建立编辑基线")
            destination = Path(tmp) / "pages"
            definition = self.stage(record, destination)
            before, after = inspect_directory(self.directory), inspect_directory(destination)
            _, registry = validated_configuration(destination)
            profile = next(p for p in registry.profiles if p.page_id == definition.page_id
                           and p.version == definition.version)
            return {"candidate_digest": digest({"draft": record.draft, "baseline": record.baseline_digest,
                                                "effective": profile_fingerprint(profile)}),
                    "effective_digest": profile_fingerprint(profile),
                    "kernel_version": KERNEL_VERSION,
                    "page_catalog": page_catalog(registry.active_profiles),
                    "page_id": definition.page_id,
                    "required_materials": sorted({m.document_type for m in profile.pack_declaration.material_policy.materials})
                    if profile.pack_declaration.material_policy and profile.pack_declaration.material_policy.mode == "enforce" else [],
                    "diff": compare_configurations(before, after),
                    "manifest": build_collect_manifest(profile.pack_declaration)}

    async def replay(self, record: SessionRecord, case_set_id: str) -> dict:
        case_path = self._case_path(case_set_id)
        with TemporaryDirectory(prefix="forj-replay-") as tmp:
            destination = Path(tmp) / "pages"
            with configuration_lock(self.directory):
                if self.baseline() != record.baseline_digest:
                    raise AgentError("BASELINE_CHANGED", "配置基线已变化")
                definition = self.stage(record, destination)
            # Do not hold a thread-local cross-process file lock across await.
            suite = ReplaySuite.model_validate_json(case_path.read_text(encoding="utf-8-sig"))
            relevant = [c for c in suite.cases if c.request.page_id == definition.page_id
                        and c.request.profile_version == definition.version]
            if not relevant:
                raise AgentError("MISSING_REPLAY", "样例必须包含目标页面和候选版本")
            result = await replay_cases(destination, case_path)
            result.update(case_set_id=case_set_id, case_digest=digest(suite.model_dump(mode="json")),
                          revision=record.revision, candidate_digest=self.validate(record)["candidate_digest"])
            return result

    def verify_replay_receipt(self, result: dict) -> bool:
        try:
            suite = ReplaySuite.model_validate_json(self._case_path(result["case_set_id"]).read_text(encoding="utf-8-sig"))
            return digest(suite.model_dump(mode="json")) == result["case_digest"]
        except (AgentError, ValueError, OSError):
            return False

    def commit(self, record: SessionRecord, operation_id: str) -> dict:
        journal = self.directory / ".transactions" / operation_id / "journal.json"
        with configuration_lock(self.directory, allow_pending=True):
            if journal.exists():
                previous = json.loads(journal.read_text(encoding="utf-8"))
                if previous["status"] == "COMMITTED":
                    return previous["receipt"]
                if previous["status"] == "ROLLED_BACK":
                    raise AgentError("COMMIT_ROLLED_BACK", "原保存操作已回滚，请重新验证并确认保存")
                raise AgentError("COMMIT_INCOMPLETE", "上次提交未完成，请执行恢复")
            if (self.directory / PENDING_COMMIT).exists() or self._prepared_journals():
                raise AgentError("COMMIT_INCOMPLETE", "存在待恢复的配置提交")
            validated = self.validate(record)
            if validated["candidate_digest"] != record.report["candidate_digest"]:
                raise AgentError("STALE_REVISION", "验证后的候选已改变")
            with TemporaryDirectory(prefix="forj-commit-") as tmp:
                stage = Path(tmp) / "pages"
                self.stage(record, stage)
                pages, _ = validated_configuration(stage)
                record_snapshot(stage, pages)
                before, after = self._files(self.directory), self._files(stage)
                receipt = {"operation_id": operation_id, "page_id": record.draft["page_id"],
                           "version": record.draft["version"], "candidate_digest": validated["candidate_digest"],
                           "effective_digest": validated["effective_digest"], "kernel_version": KERNEL_VERSION,
                           "state": "SAVED_PENDING_RESTART"}
                entry = {"status": "PREPARED", "before": before, "after": after, "receipt": receipt}
                atomic_write(journal, json.dumps(entry, ensure_ascii=False))
                atomic_write(self.directory / PENDING_COMMIT, operation_id)
                self._install(after, before)
                # Validate a separate copy while the live directory remains guarded by its marker.
                if self._files(self.directory) != after:
                    raise AgentError("COMMIT_INCOMPLETE", "配置文件摘要未达到目标")
                entry["status"] = "COMMITTED"
                atomic_write(journal, json.dumps(entry, ensure_ascii=False))
                (self.directory / PENDING_COMMIT).unlink()
                return receipt

    def commit_receipt(self, operation_id: str) -> dict | None:
        with configuration_lock(self.directory, allow_pending=True):
            path = self.directory / ".transactions" / operation_id / "journal.json"
            if not path.exists():
                return None
            entry = json.loads(path.read_text(encoding="utf-8"))
            marker = self.directory / PENDING_COMMIT
            if marker.exists():
                if marker.read_text(encoding="utf-8") != operation_id:
                    raise AgentError("COMMIT_INCOMPLETE", "另一次配置提交尚未恢复")
                self.recover()
            elif entry["status"] == "PREPARED":
                self.recover()
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry["status"] == "ROLLED_BACK":
                raise AgentError("COMMIT_ROLLED_BACK", "原保存操作已回滚，请重新验证并确认保存")
            if entry["status"] != "COMMITTED":
                raise AgentError("COMMIT_INCOMPLETE", "保存事务尚未恢复")
            return entry["receipt"]

    def _prepared_journals(self) -> list[Path]:
        return [path for path in sorted(self.directory.glob(".transactions/*/journal.json"))
                if json.loads(path.read_text(encoding="utf-8"))["status"] == "PREPARED"]

    def _install(self, target: dict, previous: dict):
        for name, content in target.items():
            atomic_write(self.directory / name, content)
        for name in previous.keys() - target.keys():
            (self.directory / name).unlink(missing_ok=True)

    def recover(self) -> dict:
        with configuration_lock(self.directory, allow_pending=True):
            marker = self.directory / PENDING_COMMIT
            if not marker.exists():
                # PREPARED can reach disk before the marker; no installation has begun.
                orphans = self._prepared_journals()
                if not orphans:
                    return {"status": "NO_PENDING_COMMIT"}
                actual = self._files(self.directory)
                entries = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in orphans]
                if any(actual != entry["before"] for _, entry in entries):
                    raise AgentError("BASELINE_CHANGED", "孤立事务与正式文件不一致，请先备份并人工对账，恢复未覆盖这些改动")
                for path, entry in entries:
                    entry["status"] = "ROLLED_BACK"
                    atomic_write(path, json.dumps(entry, ensure_ascii=False))
                return {"status": "ROLLED_BACK", "operation_id": orphans[-1].parent.name,
                        "operations": [path.parent.name for path in orphans]}
            operation_id = marker.read_text(encoding="utf-8")
            if not operation_id.isalnum():
                raise ValueError("事务标记无效")
            journal = self.directory / ".transactions" / operation_id / "journal.json"
            entry = json.loads(journal.read_text(encoding="utf-8"))
            actual = self._files(self.directory)
            before, after = entry["before"], entry["after"]
            unexpected = actual.keys() - (before.keys() | after.keys())
            missing = (before.keys() & after.keys()) - actual.keys()
            modified = [name for name, text in actual.items()
                        if text not in (before.get(name), after.get(name))]
            if unexpected or missing or modified:
                raise AgentError("BASELINE_CHANGED", "事务外的文件变更已被发现，请先备份并人工对账，恢复未覆盖这些改动")
            committed = entry["status"] == "COMMITTED"
            self._install(entry["after"] if committed else entry["before"],
                          entry["before"] if committed else entry["after"])
            with TemporaryDirectory(prefix="forj-recovery-") as tmp:
                stage = Path(tmp) / "pages"
                shutil.copytree(self.directory, stage, ignore=shutil.ignore_patterns(".transactions", PENDING_COMMIT, "*.lock"))
                validated_configuration(stage)
            entry["status"] = "COMMITTED" if committed else "ROLLED_BACK"
            atomic_write(journal, json.dumps(entry, ensure_ascii=False))
            marker.unlink()
            return {"status": entry["status"], "operation_id": operation_id}

    async def activation(self, record: SessionRecord) -> dict:
        if not record.receipt:
            return {"state": "DRAFTING"}
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(self.review_url + "/api/review/configuration/loaded")
                response.raise_for_status()
                loaded = response.json()
            match = next((p for p in loaded["pages"] if p["page_id"] == record.receipt["page_id"]), None)
            effective = (match is not None and match["version"] == record.receipt["version"]
                         and match["fingerprint"] == record.receipt["effective_digest"]
                         and loaded["kernel_version"] == KERNEL_VERSION
                         and loaded["directory"] == str(self.directory.resolve()))
            return {"state": "EFFECTIVE" if effective else "SAVED_PENDING_RESTART", "loaded": loaded,
                    "reason": "已核对加载版本" if effective else "审核服务加载目录、版本或摘要不一致"}
        except (httpx.HTTPError, ValueError, KeyError):
            return {"state": "SAVED_PENDING_RESTART", "reason": "等待审核服务启动并核对实际加载版本"}
