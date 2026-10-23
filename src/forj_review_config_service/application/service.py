"""Configuration use cases and graph nodes; all changes have durable evidence."""

import asyncio
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4
from weakref import WeakValueDictionary

from langgraph.types import Command, interrupt
from pydantic import ValidationError

from forj_review_config_service.application.adoption import prepare_adoption, verify_adoption
from forj_review_config_service.application.capture_comparison import compare_captures
from forj_review_config_service.application.context import closing_tools, model_messages
from forj_review_config_service.application.conversation import (
    fingerprint,
    normalize_reply,
    tool_message,
)
from forj_review_config_service.application.diagnostics import log_failure
from forj_review_config_service.application.drafts import apply_operations
from forj_review_config_service.application.lifecycle import archive, restore
from forj_review_config_service.application.local_reports import import_local_report
from forj_review_config_service.application.progress import emit_progress, turn_summary
from forj_review_config_service.application.rebase import merge_candidate
from forj_review_config_service.application.references import add_references
from forj_review_config_service.application.task_view import (
    candidate_export,
    task_detail,
    task_view,
)
from forj_review_config_service.application.tasks import receive_message
from forj_review_config_service.application.tool_execution import ToolExecutor
from forj_review_config_service.application.validation import (
    evaluate_trial,
    trial_binding,
    validate_candidate,
)
from forj_review_config_service.contracts.models import (
    CapturedPage,
    ConfigurationError,
    CreateTask,
    RevisionCommand,
    TaskRecord,
)
from forj_review_config_service.contracts.tasks import active_requirements, intent_digest
from forj_review_config_service.tools.ports import ConfigurationKernel, Conversation
from forj_review_config_service.workflow.graph import build_configuration_graph

logger = logging.getLogger(__name__)


class ConfigurationService:
    def __init__(
        self, store, kernel: ConfigurationKernel, model: Conversation, saver, max_calls=24
    ):
        self.store, self.kernel, self.model = store, kernel, model
        self.max_calls = max_calls
        self.executor = ToolExecutor(store, kernel, model, max_calls, self._edit)
        self.saver = saver
        self.tasks: dict[str, asyncio.Task] = {}
        self.locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self.graph = build_configuration_graph(self, saver)

    def _lock(self, sid):
        return self.locks.setdefault(sid, asyncio.Lock())

    def available_tools(self, record):
        return self.executor.available_tools(record)

    async def detail(self, sid: str) -> dict:
        record = await self.store.get(sid)
        return task_detail(record, self.kernel, self.store.database.schema)

    async def export_candidate(self, sid: str) -> dict:
        return candidate_export(await self.store.get(sid))

    async def list(self):
        result = []
        for item in await self.store.list():
            view = task_view(TaskRecord.model_validate(item.pop("task_record")))
            result.append(
                {
                    **item,
                    "label": view["label"],
                    "summary": "；".join(r["text"] for r in view["coverage"])[:160],
                }
            )
        return result

    async def validate(self, sid, command):
        async with self._lock(sid):
            record = await self.store.get(sid)
            key, scope = fingerprint(command.model_dump(mode="json")), sid + ":validate"
            cached = await self.store.cached(scope, command.client_request_id, key)
            if cached:
                return cached
            if record.revision != command.expected_revision:
                raise ConfigurationError("STALE_REVISION", "草稿版本已变化")
            if (
                command.expected_row_version is not None
                and record.row_version != command.expected_row_version
            ):
                raise ConfigurationError("STALE_TASK", "任务已更新，请刷新后重新操作")
            if record.state == "CLOSED":
                raise ConfigurationError("SESSION_ARCHIVED", "请先恢复归档任务")
            if record.turn_status in {"RUNNING", "QUEUED"}:
                raise ConfigurationError("TURN_RUNNING", "请等待当前执行结束")
            if record.receipt:
                raise ConfigurationError("SESSION_SAVED", "已保存会话请核对实际加载版本")
            if record.pending and record.pending.get("confirmed"):
                raise ConfigurationError(
                    "COMMIT_PENDING", "保存操作等待对账，请继续执行以恢复原操作"
                )
            if record.pending and record.pending["kind"] != "confirmation":
                raise ConfigurationError("INTERACTION_PENDING", "请先回答或取消待处理交互")
            record.report = await self.validate_record(record)
            record.state = "READY_FOR_CONFIRMATION" if record.report["ok"] else "DRAFTING"
            record.pending = (
                {"id": record.report["report_id"], "kind": "confirmation"}
                if record.report["ok"]
                else None
            )
            await self.saver.adelete_thread(sid)
            await self.store.put(
                record,
                "validation_completed",
                {"ok": record.report["ok"]},
                request=(scope, command.client_request_id, key, record.report),
            )
            return record.report

    async def activation(self, sid):
        async with self._lock(sid):
            record = await self.store.get(sid)
            result = await self.kernel.activation(record)
            if record.receipt:
                record.activation = {**result, "checked_at": datetime.now(UTC).isoformat()}
                if not record.adoption:
                    prepare_adoption(record)
                record.state = "PUBLISHED"
                await self.store.put(record, "activation_checked", {"state": result["state"]})
            return result

    async def _edit(self, record, operations):
        apply_operations(record, operations, self.kernel)
        record.preview = await self._preview(record)

    async def _preview(self, record):
        value = await self.kernel.validate(record)
        return {key: value[key] for key in ("diff", "candidate_digest")}

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def retain(self):
        for sid in await self.store.idle_sessions():
            record = await self.store.get(sid)
            if datetime.fromisoformat(record.updated_at) < datetime.now(UTC) - timedelta(
                days=30
            ) and not (record.pending or {}).get("confirmed"):
                try:
                    await self.command(
                        sid,
                        "archive",
                        RevisionCommand(
                            client_request_id="idle-archive-" + str(record.revision),
                            expected_revision=record.revision,
                        ),
                    )
                except ConfigurationError as exc:
                    if exc.code not in {"STALE_REVISION", "TURN_RUNNING", "SESSION_IN_USE"}:
                        raise
        await self.store.cleanup(self.saver)

    async def create(self, command: CreateTask):
        scope, key = "create", fingerprint(command.model_dump(mode="json"))
        cached = await self.store.cached(scope, command.client_request_id, key)
        if cached:
            return cached
        capture = await self._check_capture(command.capture_id, command.snapshot)
        command = command.model_copy(
            update={"capture_id": capture.capture_id, "snapshot": capture.snapshot}
        )
        fields = {f.candidate_id for f in command.snapshot.fields} if command.snapshot else set()
        samples = {s.sample_id for s in command.snapshot.samples} if command.snapshot else set()
        if set(command.reference_fields) - fields or set(command.reference_samples) - samples:
            raise ConfigurationError("INVALID_SELECTION", "引用项必须来自当前快照", 422)
        sid = uuid4().hex
        if not command.page_id:
            raise ConfigurationError(
                "EXISTING_PAGE_REQUIRED", "请选择已接入页面；新页面由开发接入", 422
            )
        if (
            command.snapshot
            and command.snapshot.detected_page_id
            and command.snapshot.detected_page_id != command.page_id
        ):
            raise ConfigurationError(
                "PAGE_MISMATCH", "现场识别的页面与目标页面不一致，请重新关联", 422
            )
        if command.parent_task_id:
            parent = await self.store.get(command.parent_task_id)
            if parent.draft["page_id"] != command.page_id:
                raise ConfigurationError("PARENT_PAGE_MISMATCH", "关联任务必须属于同一页面", 422)
        current = await self.kernel.context(command.page_id)
        draft = deepcopy(current["editable"])
        draft["version"] = datetime.now(UTC).strftime("%Y%m%d.%H%M%S.") + sid[:6]
        draft["active"] = True
        if command.rollback_version:
            draft = await self.kernel.rollback_draft(command.page_id, command.rollback_version)
        record = TaskRecord(
            task_id=sid,
            model_id=getattr(getattr(self.model, "settings", None), "model", "test"),
            max_model_calls=self.max_calls,
            snapshot=command.snapshot,
            capture_id=command.capture_id,
            activation_only=bool(command.rollback_version),
            reference_fields=command.reference_fields,
            reference_samples=command.reference_samples,
            parent_task_id=command.parent_task_id,
            baseline_digest=(await self.kernel.baseline(command.page_id)),
            draft=draft,
        )
        record.preview = await self._preview(record)
        if command.initial_message:
            receive_message(record, command.initial_message)
            record.turn_id, record.turn_status = uuid4().hex, "QUEUED"
        result = {"task_id": sid}
        await self.store.put(
            record,
            "session_created",
            result,
            request=(scope, command.client_request_id, key, result),
        )
        if command.initial_message:
            task = asyncio.create_task(self._run(sid), name=f"configuration-session-{sid}")
            self.tasks[sid] = task
            task.add_done_callback(lambda done: self._task_finished(sid, done))
        return result

    async def rebase_preview(self, record):
        current = await self.kernel.context(record.draft["page_id"])
        baseline = await self.kernel.baseline(record.draft["page_id"])
        if record.baseline_version is None:
            raise ConfigurationError(
                "BASELINE_UNAVAILABLE", "任务缺少原页面版本关联，请保留需求创建后续任务"
            )
        base = await self.kernel.rollback_draft(record.draft["page_id"], record.baseline_version)
        candidate, conflicts = merge_candidate(base, record.draft, current["editable"])
        return {
            "baseline_digest": baseline,
            "candidate": candidate,
            "conflicts": conflicts,
            "current_version": current["reference"]["version"],
        }

    async def _fork_task(self, record, kind, command):
        await self._check_capture(record.capture_id, record.snapshot)
        if (
            record.receipt
            or record.state == "CLOSED"
            or (
                record.pending
                and (record.pending["kind"] != "confirmation" or record.pending.get("confirmed"))
            )
        ):
            raise ConfigurationError(
                "INTERACTION_PENDING", "请先处理当前交互；已发布或归档任务请从活动版本新建"
            )
        current = await self.kernel.context(record.draft["page_id"])
        baseline = await self.kernel.baseline(record.draft["page_id"])
        requirements = active_requirements(record)
        candidate = deepcopy(current["editable"])
        if kind == "rebase":
            preview = await self.rebase_preview(record)
            if preview["baseline_digest"] != command.baseline_digest:
                raise ConfigurationError("BASELINE_CHANGED", "活动基线再次变化，请重新查看冲突")
            paths = {c["path"] for c in preview["conflicts"]}
            if set(command.resolutions) != paths:
                raise ConfigurationError("UNRESOLVED_CONFLICTS", "请逐项选择冲突保留值后继续")
            candidate, _ = merge_candidate(
                await self.kernel.rollback_draft(record.draft["page_id"], record.baseline_version),
                record.draft,
                current["editable"],
                command.resolutions,
            )
        else:
            selected = set(command.requirement_ids)
            if (
                len(selected) != len(command.requirement_ids)
                or not selected < {r["requirement_id"] for r in requirements}
                or any(r.get("status") == "PENDING" for r in requirements)
            ):
                raise ConfigurationError(
                    "INVALID_SPLIT", "只能拆分已明确的部分有效需求；原任务保留全部目标"
                )
            requirements = [r for r in requirements if r["requirement_id"] in selected]
            if any(set(r.get("depends_on", [])) - selected for r in requirements):
                raise ConfigurationError(
                    "DEPENDENT_SPLIT", "所选目标依赖未选目标，请包含依赖项或先澄清独立验收边界"
                )
        sid = fingerprint([record.task_id, kind, command.client_request_id])[:32]
        try:
            await self.store.get(sid)
        except ConfigurationError as exc:
            if exc.code != "NOT_FOUND":
                raise
            candidate["version"] = datetime.now(UTC).strftime("%Y%m%d.%H%M%S.") + sid[:6]
            copied_requirements = deepcopy(requirements)
            ids = {r["requirement_id"]: "req_" + uuid4().hex for r in copied_requirements}
            source_id = "message_" + uuid4().hex
            for requirement in copied_requirements:
                requirement["requirement_id"] = ids[requirement["requirement_id"]]
                requirement["source_event_id"] = source_id
                requirement["last_changed_event_id"] = source_id
                requirement["depends_on"] = [
                    ids[key] for key in requirement.get("depends_on", []) if key in ids
                ]
                requirement["replaces"] = []
            child = TaskRecord(
                task_id=sid,
                parent_task_id=record.task_id,
                baseline_digest=baseline,
                draft=candidate,
                capture_id=record.capture_id,
                snapshot=record.snapshot,
                requirements=copied_requirements,
                messages=[
                    {
                        "role": "user",
                        "content": "从原任务明确接续的目标：\n"
                        + "\n".join(r["text"] for r in requirements),
                        "_event_id": source_id,
                    }
                ],
                intent_revision=record.intent_revision,
            )
            child.preview = await self._preview(child)
            await self.store.put(
                child,
                "task_forked",
                {
                    "parent_task_id": record.task_id,
                    "kind": kind,
                    "independence_reason": getattr(command, "independence_reason", None),
                },
            )
        record.child_tasks.append(
            {
                "task_id": sid,
                "kind": kind,
                "requirement_ids": [r["requirement_id"] for r in requirements],
                "reason": getattr(command, "independence_reason", "更新基线并保留修改目标"),
            }
        )
        return sid

    async def capture(self, command):
        key = fingerprint(command.model_dump(mode="json"))
        cached = await self.store.cached("capture", command.client_request_id, key)
        if cached:
            return cached
        capture = CapturedPage(
            capture_id=uuid4().hex,
            extension_id=command.extension_id,
            snapshot=command.snapshot,
            created_at=datetime.now(UTC).isoformat(),
        )
        return await self.store.put_capture(capture, command.client_request_id, key)

    async def _check_capture(self, capture_id, snapshot):
        if not capture_id and snapshot is None:
            raise ConfigurationError("CAPTURE_REQUIRED", "请先采集页面或选择已保存的历史现场", 422)
        if not capture_id:
            # Inline directory inputs use the same immutable storage as extension captures.
            key = fingerprint(snapshot.model_dump(mode="json"))
            cached = await self.store.cached("capture", "inline-" + key, key)
            if cached:
                capture_id = cached["capture_id"]
            else:
                capture = CapturedPage(
                    capture_id=uuid4().hex,
                    extension_id="local",
                    snapshot=snapshot,
                    created_at=datetime.now(UTC).isoformat(),
                )
                await self.store.put_capture(capture, "inline-" + key, key)
                capture_id = capture.capture_id
        capture = await self.store.get_capture(capture_id)
        if snapshot is not None and capture.snapshot != snapshot:
            raise ConfigurationError("CAPTURE_MISMATCH", "页面现场与采集记录不一致，请重新加载采集")
        return capture

    async def command(self, sid, kind, command, interaction_id=None):
        async with self._lock(sid):
            record = await self.store.get(sid)
            payload = command.model_dump(mode="json")
            key, scope = fingerprint(payload), sid + ":" + kind + ":" + str(interaction_id or "")
            cached = await self.store.cached(scope, command.client_request_id, key)
            if cached:
                return cached
            if (
                command.expected_row_version is not None
                and record.row_version != command.expected_row_version
            ):
                raise ConfigurationError("STALE_TASK", "任务已更新，请刷新后重新操作")
            if kind in {"message", "resume", "answer"} and not (record.pending or {}).get(
                "confirmed"
            ):
                await self._check_capture(record.capture_id, record.snapshot)
            if (
                kind in {"message", "rebase", "split"}
                and command.expected_intent_revision is not None
                and command.expected_intent_revision != record.intent_revision
            ):
                raise ConfigurationError("STALE_INTENT", "修改要求已变化，请刷新后重新发送")
            task = self.tasks.get(sid)
            if kind == "message" and task and not task.done():
                if (record.pending or {}).get("confirmed"):
                    raise ConfigurationError(
                        "COMMIT_PENDING", "发布已提交，请完成对账后新建后续任务"
                    )
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                record = await self.store.get(sid)
                if (
                    command.expected_intent_revision is not None
                    and command.expected_intent_revision != record.intent_revision
                ):
                    raise ConfigurationError(
                        "STALE_INTENT", "执行期间需求版本已更新，请刷新后重新发送"
                    )
            if record.revision != command.expected_revision and not (
                kind == "message"
                and command.expected_intent_revision is not None
                and command.expected_revision <= record.revision
            ):
                raise ConfigurationError("STALE_REVISION", "草稿已变化，请刷新后重试")
            if sid in self.tasks and not self.tasks[sid].done():
                raise ConfigurationError("TURN_RUNNING", "当前轮次正在执行，请稍候或取消")
            if record.pending and record.pending.get("confirmed") and kind != "resume":
                raise ConfigurationError(
                    "COMMIT_PENDING", "保存操作等待对账，请继续执行以恢复原操作"
                )
            if (
                kind in {"message", "edit", "mappings", "trial", "replay", "references"}
                and record.receipt
            ):
                raise ConfigurationError(
                    "SESSION_SAVED", "此会话已经保存，请从正式版本新建修改会话"
                )
            start = False
            child_id = None
            if record.state == "CLOSED" and kind not in {"restore", "archive"}:
                raise ConfigurationError("SESSION_ARCHIVED", "请先恢复归档任务")
            if kind in {"mappings", "trial", "replay"}:
                if record.pending and record.pending["kind"] != "confirmation":
                    raise ConfigurationError("INTERACTION_PENDING", "请先完成或取消待处理交互")
                if record.pending:
                    await self.saver.adelete_thread(sid)
                record.pending, record.report, record.state = None, None, "DRAFTING"
            if kind in {"rebase", "split"}:
                child_id = await self._fork_task(record, kind, command)
            elif kind == "local-report":
                import_local_report(record, command.report)
            elif kind in {"archive", "restore"}:
                archive(record) if kind == "archive" else restore(record)
                await self.saver.adelete_thread(sid)
            elif kind == "references":
                if record.pending:
                    raise ConfigurationError(
                        "INTERACTION_PENDING", "请先处理当前交互，再调整参考资料"
                    )
                add_references(record, command.fields, command.samples)
                record.state = "DRAFTING"
            elif kind == "adoption":
                await verify_adoption(record, command, self.kernel)
            elif kind == "message":
                completed = {
                    m.get("tool_call_id") for m in record.messages if m.get("role") == "tool"
                }
                for message in list(record.messages):
                    for call in message.get("tool_calls", []):
                        if call["id"] not in completed:
                            result = {"status": "CANCELLED", "reason": "用户补充或更正了本次要求"}
                            record.tool_results[call["id"]] = result
                            self._tool_message(record, call["id"], result)
                await self.saver.adelete_thread(sid)
                record.pending = None
                add_references(record, command.reference_fields, command.reference_samples)
                receive_message(record, command.text)
                start = True
            elif kind == "edit":
                if record.pending and record.pending["kind"] != "confirmation":
                    raise ConfigurationError("INTERACTION_PENDING", "请先回答或取消待处理交互")
                if record.pending:
                    await self.saver.adelete_thread(sid)
                await self._edit(record, command.operations)
            elif kind == "mappings":
                preview = await self.kernel.validate(record)
                fields = {f["key"] for f in preview["manifest"]["fields"]}
                materials = {m["document_type"] for m in preview["manifest"]["materials"]}
                if (
                    set(command.fields) - set(record.reference_fields)
                    or set(command.materials) - set(record.reference_samples)
                    or set(command.fields.values()) - fields
                    or set(command.materials.values()) - materials
                    or set(command.requirements)
                    - {r["requirement_id"] for r in record.requirements}
                ):
                    raise ConfigurationError(
                        "INVALID_MAPPING", "映射必须对应已选现场和候选字段、材料", 422
                    )
                record.evidence_revision += 1
                record.decisions = [
                    d for d in record.decisions if not (d.get("candidate_id") or d.get("sample_id"))
                ]
                for identity, values, target in (
                    ("candidate_id", command.fields, "field_key"),
                    ("sample_id", command.materials, "material"),
                ):
                    for source, destination in values.items():
                        record.decisions.append(
                            {
                                "decision_id": uuid4().hex,
                                "revision": record.revision,
                                "status": "RESOLVED",
                                identity: source,
                                target: destination,
                                "reason": "操作人员确认现场映射",
                                "evidence_refs": [source],
                            }
                        )
                for requirement_id in command.requirements:
                    record.decisions.append(
                        {
                            "decision_id": uuid4().hex,
                            "revision": record.revision,
                            "status": "RESOLVED",
                            "requirement_id": requirement_id,
                            "intent_revision": record.intent_revision,
                            "reason": "操作人员核对草稿后确认要求已处理",
                            "evidence_refs": [requirement_id],
                        }
                    )
            elif kind == "trial":
                if record.snapshot is None:
                    raise ConfigurationError("CAPTURE_REQUIRED", "请先采集并关联当前业务页面")
                preview = await self.kernel.validate(record)
                task_id = "manual_" + uuid4().hex
                record.pending = {
                    "id": task_id,
                    "tool_call_id": task_id,
                    "revision": record.revision,
                    "kind": "browser",
                    "manual": True,
                    "binding": trial_binding(record),
                    "manifest": preview["manifest"],
                    "candidate_digest": preview["candidate_digest"],
                    "page_catalog": preview["page_catalog"],
                    "page_id": preview["page_id"],
                    "snapshot_id": record.snapshot.snapshot_id,
                    "structure_digest": record.snapshot.structure_digest,
                    "page_instance_id": record.snapshot.page_instance_id,
                    "page_url": record.snapshot.page_url,
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                }
            elif kind == "replay":
                replay = await self.kernel.replay(record, command.case_set_id)
                record.tool_results["manual_replay_" + uuid4().hex] = replay
            elif kind == "page":
                capture = await self._check_capture(command.capture_id, command.snapshot)
                command = command.model_copy(
                    update={"capture_id": capture.capture_id, "snapshot": capture.snapshot}
                )
                if (
                    command.snapshot.detected_page_id
                    and command.snapshot.detected_page_id != record.draft["page_id"]
                ):
                    raise ConfigurationError("PAGE_MISMATCH", "现场识别的页面与当前任务不一致", 422)
                fields = {f.candidate_id for f in command.snapshot.fields}
                samples = {s.sample_id for s in command.snapshot.samples}
                if (
                    set(command.reference_fields) - fields
                    or set(command.reference_samples) - samples
                ):
                    raise ConfigurationError("INVALID_SELECTION", "所选范围不属于新快照", 422)
                if record.pending and record.pending.get("tool_call_id"):
                    call_id = record.pending["tool_call_id"]
                    result = {"status": "STALE", "reason": "操作人员替换了页面现场"}
                    record.tool_results[call_id] = result
                    self._tool_message(record, call_id, result)
                record.capture_transition = (
                    compare_captures(record.snapshot, command.snapshot) if record.snapshot else None
                )
                for decision in record.decisions:
                    decision["status"] = "SUPERSEDED"
                record.snapshot = command.snapshot
                record.capture_id = command.capture_id
                record.reference_fields, record.reference_samples = (
                    command.reference_fields,
                    command.reference_samples,
                )
                record.evidence_revision += 1
                record.pending, record.report = None, None
                record.adoption, record.adoption_task = None, None
                record.waiting_reason = None
                record.state = "PUBLISHED" if record.receipt else "DRAFTING"
                await self.saver.adelete_thread(sid)
                if record.receipt:
                    record.activation = await self.kernel.activation(record)
                    prepare_adoption(record)
                elif record.requirements:
                    start = True
            elif kind == "answer":
                if (
                    not record.pending
                    or record.pending["id"] != interaction_id
                    or record.pending["kind"] != "question"
                    or record.pending.get("intent_revision", record.intent_revision)
                    != record.intent_revision
                ):
                    raise ConfigurationError("STALE_INTERACTION", "问题已经过期")
                add_references(record, command.reference_fields, command.reference_samples)
                record.pending["answer"] = command.answer
                start = True
            elif kind == "browser":
                pending = record.pending or {}
                if pending.get("id") != interaction_id or pending.get("kind") != "browser":
                    raise ConfigurationError("STALE_INTERACTION", "浏览器任务已过期")
                if datetime.now(UTC) > datetime.fromisoformat(pending["expires_at"]):
                    raise ConfigurationError(
                        "STALE_PAGE", "浏览器任务已过期，请重新采集并更新会话现场"
                    )
                for attr in (
                    "snapshot_id",
                    "page_instance_id",
                    "page_url",
                    "structure_digest",
                    "candidate_digest",
                ):
                    if getattr(command, attr) != pending[attr]:
                        raise ConfigurationError(
                            "STALE_PAGE", "浏览器页面或候选与任务不一致，请重新采集"
                        )
                if pending.get("binding") != trial_binding(record):
                    raise ConfigurationError("STALE_PAGE", "页面现场或范围已变化，请重新试采集")
                facts = command.model_dump(
                    mode="json", exclude={"client_request_id", "expected_revision"}
                )
                pending["result"] = {
                    **evaluate_trial(record, pending["manifest"], facts, self.kernel),
                    "facts": facts,
                }
                if pending.get("manual"):
                    record.tool_results[pending["id"]] = {
                        "kind": "collection_trial",
                        "binding": pending["binding"],
                        "expires_at": pending["expires_at"],
                        "candidate_digest": pending["candidate_digest"],
                        **pending["result"],
                    }
                    record.pending = None
                else:
                    start = True
            elif kind == "resume":
                confirmed = record.pending and record.pending.get("confirmed")
                if record.receipt and not confirmed:
                    raise ConfigurationError("SESSION_SAVED", "已保存会话无需恢复执行")
                if (
                    not confirmed
                    and (await self.kernel.baseline(record.draft["page_id"]))
                    != record.baseline_digest
                ):
                    raise ConfigurationError(
                        "BASELINE_CHANGED", "基础配置已变化，请基于新版本创建会话"
                    )
                start = True
            elif kind == "confirm":
                report = record.report or {}
                source = (await self.kernel.context(record.draft["page_id"]))["source"]
                if not record.activation_only and {
                    k: v for k, v in source.items() if k not in {"version", "active"}
                } == {k: v for k, v in record.draft.items() if k not in {"version", "active"}}:
                    raise ConfigurationError(
                        "NO_CONFIGURATION_CHANGE", "当前配置已满足候选内容，无需发布空版本"
                    )
                if (
                    record.state != "READY_FOR_CONFIRMATION"
                    or not report.get("ok")
                    or report.get("report_id") != command.report_id
                    or report.get("candidate_digest") != command.candidate_digest
                    or report.get("intent_digest", intent_digest(record)) != intent_digest(record)
                ):
                    raise ConfigurationError("STALE_CONFIRMATION", "候选或验证报告已失效")
                # Store a trustworthy HTTP confirmation fact, never accept approval from model output.
                record.pending = {
                    "id": command.client_request_id,
                    "kind": "confirmation",
                    "confirmed": True,
                    "intent_digest": intent_digest(record),
                    "intent_revision": record.intent_revision,
                    "candidate_digest": command.candidate_digest,
                    "operation_id": fingerprint([sid, command.client_request_id]),
                }
                start = True
            if start and (kind not in {"answer", "browser"} or record.turn_id is None):
                # A continuation of a question/browser wait keeps its round and budget.
                # New input, recovery and publication each start a durable execution round.
                record.turn_id = uuid4().hex
                record.model_id = getattr(getattr(self.model, "settings", None), "model", "test")
                record.max_model_calls = self.max_calls
                record.model_calls, record.tool_calls = 0, 0
                record.failure_counts, record.usage = {}, {}
            result = {"task_id": sid, "turn_id": record.turn_id, "revision": record.revision}
            if child_id:
                result["task_id"] = child_id
            if start:
                record.turn_status, record.error, record.error_code = "QUEUED", None, None
            await self.store.put(
                record,
                "command_accepted",
                {"kind": kind},
                request=(scope, command.client_request_id, key, result),
            )
            if start:
                task = asyncio.create_task(self._run(sid), name=f"configuration-session-{sid}")
                self.tasks[sid] = task
                task.add_done_callback(lambda done: self._task_finished(sid, done))
            return result

    def _task_finished(self, sid, task):
        if self.tasks.get(sid) is task:
            self.tasks.pop(sid, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("configuration task failed task_id=%s", sid, exc_info=task.exception())

    async def cancel(self, sid):
        async with self._lock(sid):
            current = await self.store.get(sid)
            if current.receipt:
                raise ConfigurationError(
                    "SESSION_SAVED", "配置已保存，不能通过取消撤回；请使用历史版本回退"
                )
            if current.pending and current.pending.get("confirmed"):
                raise ConfigurationError(
                    "COMMIT_PENDING", "已确认的保存操作需要完成对账，请继续执行"
                )
            task = self.tasks.get(sid)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            record = await self.store.get(sid)
            pending = record.pending
            if pending and pending.get("tool_call_id"):
                self._tool_message(record, pending["tool_call_id"], {"status": "CANCELLED"})
            record.pending = None
            if record.turn_id is None:
                record.turn_id = uuid4().hex
            record.turn_status = "CANCELLED"
            if record.state != "PUBLISHED":
                record.state = "DRAFTING"
            await emit_progress(
                self.store,
                record,
                "turn_stopped",
                {"status": "CANCELLED", "activity": turn_summary(record)},
            )
            await self.saver.adelete_thread(sid)

    async def delete(self, sid):
        async with self._lock(sid):
            await self.store.delete(sid)
            await self.saver.adelete_thread(sid)
            self.tasks.pop(sid, None)

    async def _run(self, sid):
        config = {"configurable": {"thread_id": sid}, "recursion_limit": self.max_calls * 6 + 20}
        try:
            record = await self.store.get(sid)
            record.turn_status = "RUNNING"
            await emit_progress(
                self.store,
                record,
                "turn_started",
                {
                    "turn_id": record.turn_id,
                    "model_calls": record.model_calls,
                    "activity": {
                        "title": "开始处理本轮需求",
                        "detail": "核对已有目标、配置基线与现场依据。",
                    },
                },
            )
            snapshot = await self.graph.aget_state(config)
            value = (
                Command(resume=True)
                if snapshot.tasks and any(t.interrupts for t in snapshot.tasks)
                else {"task_id": sid}
            )
            async with asyncio.timeout(900):
                await self.graph.ainvoke(value, config)
            record = await self.store.get(sid)
            record.turn_status = "WAITING" if record.pending else "COMPLETED"
            await emit_progress(
                self.store,
                record,
                "turn_stopped",
                {"status": record.turn_status, "activity": turn_summary(record)},
            )
        except asyncio.CancelledError:
            if self.store.database.lost.is_set():
                raise
            record = await self.store.get(sid)
            record.turn_status = "INTERRUPTED"
            await emit_progress(
                self.store,
                record,
                "turn_stopped",
                {"status": "INTERRUPTED", "activity": turn_summary(record)},
            )
            raise
        except Exception as exc:
            if self.store.database.lost.is_set():
                logger.error("configuration executor lost ownership task_id=%s", sid)
                return
            record = await self.store.get(sid)
            record.turn_status = (
                "INTERRUPTED"
                if isinstance(exc, ConfigurationError)
                and exc.code in {"BUDGET_EXCEEDED", "EXECUTION_STALLED"}
                else "FAILED"
            )
            record.error = (
                exc.message
                if isinstance(exc, ConfigurationError)
                else "执行失败，可恢复会话；请查看配置服务日志中的错误类型"
            )
            record.error_code = (
                exc.code if isinstance(exc, ConfigurationError) else "EXECUTION_FAILED"
            )
            await emit_progress(
                self.store,
                record,
                "turn_stopped",
                {
                    "status": record.turn_status,
                    "error": record.error,
                    "code": record.error_code,
                    "activity": turn_summary(record),
                },
            )
            log_failure(
                logger, exc, task_id=sid, turn_id=record.turn_id, node="configuration_graph"
            )

    async def load_context(self, state):
        record = await self.store.get(state["task_id"])
        if record.receipt:
            if record.pending:
                record.pending = None
                await self.store.put(record, "configuration_reconciled", record.receipt)
            return {"route": "end"}
        if record.pending and record.pending.get("confirmed"):
            return {"route": "await_confirmation"}
        if (await self.kernel.baseline(record.draft["page_id"])) != record.baseline_digest:
            raise ConfigurationError("BASELINE_CHANGED", "配置基线发生变化，请重新建立会话")
        if record.pending:
            kind = record.pending["kind"]
            return {
                "route": {
                    "question": "await_user",
                    "browser": "await_browser",
                    "confirmation": "await_confirmation",
                }[kind]
            }
        # A crash after persisting an assistant call must resume that call, not ask a fresh model.
        last_calls = next(
            (m["tool_calls"] for m in reversed(record.messages) if m.get("tool_calls")), []
        )
        completed = {m.get("tool_call_id") for m in record.messages if m.get("role") == "tool"}
        if any(call["id"] not in completed for call in last_calls):
            return {"route": "execute_tools"}
        return {"route": "reason"}

    async def reason(self, state):
        record = await self.store.get(state["task_id"])
        if record.model_calls >= self.max_calls:
            progress = "已有修改已保留" if record.revision else "尚未生成配置修改"
            raise ConfigurationError("BUDGET_EXCEEDED", f"本轮模型预算已耗尽；{progress}，可以恢复")
        record.model_calls += 1
        model_call_id = uuid4().hex
        await emit_progress(
            self.store,
            record,
            "model_started",
            {
                "model_call_id": model_call_id,
                "model_calls": record.model_calls,
                "activity": {
                    "title": "等待配置助手返回",
                    "detail": "结合当前需求和已取得的依据确定下一步。",
                },
            },
        )
        messages = model_messages(record, max_calls=self.max_calls)
        started = perf_counter()
        tools = closing_tools(record, self.available_tools(record), self.max_calls)
        try:
            reply = await self.model.complete(messages, tools)
            message = normalize_reply(reply)
        except Exception as exc:
            await emit_progress(
                self.store,
                record,
                "model_failed",
                {
                    "model_call_id": model_call_id,
                    "status": "FAILED",
                    "code": exc.code
                    if isinstance(exc, ConfigurationError)
                    else "MODEL_REQUEST_FAILED",
                    "duration_ms": int((perf_counter() - started) * 1000),
                    "activity": {
                        "title": "配置模型请求失败",
                        "detail": "本次模型请求未完成，已有依据与草稿保留；请核对错误码和服务日志。",
                    },
                },
            )
            raise
        message["_run_id"] = record.turn_id
        message["_allowed_tools"] = [tool["function"]["name"] for tool in tools]
        usage = reply.get("_usage", {})
        for key, value in usage.items():
            record.usage[key] = record.usage.get(key, 0) + value
        if not message.get("content") and not message.get("tool_calls"):
            raise ConfigurationError("MODEL_FORMAT", "模型返回空内容，可以重试", 422)
        record.messages.append(message)
        await emit_progress(
            self.store,
            record,
            "model_completed",
            {
                "model_call_id": model_call_id,
                "duration_ms": int((perf_counter() - started) * 1000),
                "usage": usage,
            },
        )
        await self.store.put(record, "assistant_message", {"text": message.get("content", "")})
        return {"route": "execute_tools" if message.get("tool_calls") else "end"}

    _tool_message = staticmethod(tool_message)

    async def execute_tools(self, state):
        return await self.executor.execute_tools(state)

    async def await_user(self, state):
        sid = state["task_id"]
        record = await self.store.get(sid)
        if record.pending and "answer" not in record.pending:
            interrupt({"kind": "question", "id": record.pending["id"]})
        record = await self.store.get(sid)
        if record.pending:
            pending = record.pending
            result = {"answer": pending["answer"]}
            record.tool_results[pending["tool_call_id"]] = result
            self._tool_message(record, pending["tool_call_id"], result)
            record.pending, record.state = None, "DRAFTING"
            receive_message(
                record, "针对问题「" + pending["question"] + "」的补充：" + pending["answer"]
            )
            record.messages[-1]["_interaction_answer"] = True
            await self.store.put(record, "question_answered")
        return {"route": "reason"}

    async def await_browser(self, state):
        sid = state["task_id"]
        record = await self.store.get(sid)
        if record.pending and "result" not in record.pending:
            interrupt({"kind": "browser", "id": record.pending["id"]})
        record = await self.store.get(sid)
        if record.pending:
            pending = record.pending
            result = {
                **pending["result"],
                "kind": "collection_trial",
                "revision": pending["revision"],
                "candidate_digest": pending["candidate_digest"],
                "binding": pending.get("binding"),
                "expires_at": pending["expires_at"],
            }
            record.tool_results[pending["tool_call_id"]] = result
            self._tool_message(record, pending["tool_call_id"], result)
            record.pending = None
            await self.store.put(record, "browser_task_completed")
        return {"route": "reason"}

    async def validate_record(self, record):
        return await validate_candidate(self.kernel, record)

    async def verify_candidate(self, state):
        record = await self.store.get(state["task_id"])
        try:
            record.report = await self.validate_record(record)
        except (ValidationError, ConfigurationError) as exc:
            if isinstance(exc, ConfigurationError) and exc.code == "BASELINE_CHANGED":
                raise
            record.report = {
                "report_id": uuid4().hex,
                "revision": record.revision,
                "candidate_digest": fingerprint(record.draft),
                "ok": False,
                "blockers": [
                    exc.message
                    if isinstance(exc, ConfigurationError)
                    else "候选配置无效，请检查字段、依赖与配置格式"
                ],
                "replays": [],
                "diff": {},
            }
        record.state = "READY_FOR_CONFIRMATION" if record.report["ok"] else "DRAFTING"
        record.messages.append(
            {
                "role": "user",
                "content": "系统强制验证结果："
                + json.dumps(
                    {k: record.report[k] for k in ("report_id", "ok", "blockers")},
                    ensure_ascii=False,
                ),
            }
        )
        await self.store.put(record, "validation_completed", {"ok": record.report["ok"]})
        if record.report["ok"]:
            record.pending = {"id": record.report["report_id"], "kind": "confirmation"}
            await self.store.put(record, "confirmation_pending")
            return {"route": "await_confirmation"}
        return {"route": "reason"}

    async def await_confirmation(self, state):
        record = await self.store.get(state["task_id"])
        if not record.pending:
            return {"route": "end"}
        if not record.pending.get("confirmed"):
            interrupt({"kind": "confirmation", "report_id": (record.report or {}).get("report_id")})
        return {"route": "commit_configuration"}

    async def _revoke_confirmation(self, record, reason, message):
        operation_id = record.pending["operation_id"]
        record.pending, record.report, record.state = None, None, "DRAFTING"
        record.error = message
        await self.store.put(
            record, "confirmation_revoked", {"operation_id": operation_id, "reason": reason}
        )

    async def commit_configuration(self, state):
        record = await self.store.get(state["task_id"])
        if not record.pending or not record.pending.get("confirmed"):
            raise ConfigurationError("CONFIRMATION_REQUIRED", "缺少用户确认事件")
        # The release transaction may have completed before graph reconciliation.
        try:
            existing = await self.kernel.commit_receipt(record.pending["operation_id"])
        except ConfigurationError as exc:
            if exc.code != "COMMIT_ROLLED_BACK":
                raise
            await self._revoke_confirmation(
                record, exc.code, "上次保存已安全回滚，草稿保留。请重新验证配置并确认保存。"
            )
            return
        if existing:
            record.receipt, record.state, record.pending = existing, "PUBLISHED", None
            await self.store.put(record, "configuration_saved", existing)
            return
        try:
            report = await self.validate_record(record)
        except (ConfigurationError, ValueError):
            # No transaction exists yet, so an invalid approval can safely be revoked.
            await self._revoke_confirmation(
                record, "STALE_CONFIRMATION", "保存前验证失败，原确认已失效，草稿保留。"
            )
            raise
        if (
            not report["ok"]
            or report["candidate_digest"] != record.pending["candidate_digest"]
            or record.pending.get("intent_digest", intent_digest(record)) != intent_digest(record)
        ):
            await self._revoke_confirmation(
                record, "STALE_CONFIRMATION", "保存前验证失败，原确认已失效，请重新验证。"
            )
            raise ConfigurationError("STALE_CONFIRMATION", "保存前验证失败")
        record.receipt = await self.kernel.commit(record, record.pending["operation_id"])
        record.state, record.pending = "PUBLISHED", None
        await self.store.put(record, "configuration_saved", record.receipt)
