"""Strict model tool execution; business operations stay in the configuration backend."""

import json
import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter

from forj_review_config_service.application.conversation import fingerprint, tool_message
from forj_review_config_service.application.diagnostics import log_failure
from forj_review_config_service.application.progress import emit_progress, tool_activity
from forj_review_config_service.application.references import add_references
from forj_review_config_service.application.tasks import manage_requirements
from forj_review_config_service.application.validation import trial_binding
from forj_review_config_service.contracts.models import (
    CapabilityGap,
    ConfigurationError,
    DraftOperation,
)
from forj_review_config_service.contracts.tasks import active_requirements
from forj_review_config_service.tools.arguments import validate_arguments
from forj_review_config_service.tools.catalog import configuration_tools

logger = logging.getLogger(__name__)

READ_TOOLS = {
    "get_configuration",
    "get_configuration_capabilities",
    "read_draft",
    "get_page_snapshot",
    "read_tool_result",
    "analyze_materials",
    "inspect_material_region",
    "find_templates",
}


def changed_values(before, after, path=""):
    """Show actual leaf changes without repeating entire effective page snapshots."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            change
            for key in sorted(before.keys() | after.keys())
            for change in changed_values(before.get(key), after.get(key), f"{path}/{key}")
        ]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            change
            for i, (old, new) in enumerate(zip(before, after, strict=True))
            for change in changed_values(old, new, f"{path}/{i}")
        ]
    return [{"path": path, "before": before, "after": after}]


class ToolExecutor:
    def __init__(self, store, kernel, model, max_calls, edit):
        self.store, self.kernel, self.model = store, kernel, model
        self.max_calls, self.edit = max_calls, edit

    def available_tools(self, record):
        return [
            tool
            for tool in configuration_tools()
            if record.snapshot
            and record.snapshot.samples
            or tool["function"]["name"] not in {"analyze_materials", "inspect_material_region"}
        ]

    def cached_read(self, record, name, cache_key):
        if name in READ_TOOLS:
            for key, value in record.tool_results.items():
                if (
                    isinstance(value, dict)
                    and value.get("_execution", {}).get("cache_key") == cache_key
                ):
                    return key, value
        return None

    async def execute_tools(self, state):
        sid = state["task_id"]
        record = await self.store.get(sid)
        message = next(m for m in reversed(record.messages) if m.get("tool_calls"))
        calls = message["tool_calls"]
        for call in calls:
            record = await self.store.get(sid)
            call_id, name = call["id"], call["function"]["name"]
            if call_id in record.tool_results:
                tool_message(record, call_id, record.tool_results[call_id])
                await self.store.put(record)
                continue
            if record.tool_calls >= 120:
                raise ConfigurationError("BUDGET_EXCEEDED", "本轮工具预算耗尽，草稿已保留")
            record.tool_calls += 1
            started = perf_counter()
            args = {}
            try:
                raw_args = json.loads(call["function"]["arguments"])
                if name not in {t["function"]["name"] for t in self.available_tools(record)}:
                    raise ConfigurationError("UNKNOWN_TOOL", "未注册或当前范围不可用的工具", 422)
                args = validate_arguments(name, raw_args)
                await emit_progress(
                    self.store,
                    record,
                    "tool_started",
                    {
                        "name": name,
                        "call_id": call_id,
                        "arguments_digest": fingerprint(args),
                        "activity": tool_activity(name, args),
                    },
                )
                if "_allowed_tools" in message and name not in message["_allowed_tools"]:
                    raise ConfigurationError(
                        "EXECUTION_STALLED",
                        "调查未能收口，本轮已暂停；已保留依据和草稿，请核对具体缺口后继续",
                    )
                if (
                    name in {"ask_user", "request_collection_trial", "submit_candidate"}
                    and len(calls) != 1
                ):
                    raise ConfigurationError(
                        "INVALID_TOOL_BATCH", "暂停或提交工具必须单独调用", 422
                    )
                cache_key = fingerprint(
                    [
                        record.turn_id,
                        name,
                        args,
                        record.revision,
                        record.intent_revision,
                        record.evidence_revision,
                    ]
                )
                cached = self.cached_read(record, name, cache_key)
                result = (
                    {**cached[1], "_execution": {"cache_key": cache_key, "reused_from": cached[0]}}
                    if cached
                    else await self._execute(record, name, args, call_id)
                )
                if name in READ_TOOLS and not cached:
                    result = {**result, "_execution": {"cache_key": cache_key}}
                if name not in record.investigated_tools:
                    record.investigated_tools.append(name)
                if record.pending:
                    await emit_progress(
                        self.store,
                        record,
                        "question_pending" if name == "ask_user" else "browser_task_pending",
                        {
                            "name": name,
                            "call_id": call_id,
                            "status": "WAITING",
                            "duration_ms": int((perf_counter() - started) * 1000),
                            "activity": tool_activity(name, args, {}),
                        },
                    )
                    return {"route": "await_user" if name == "ask_user" else "await_browser"}
                record.tool_results[call_id] = result
                tool_message(record, call_id, result)
                await emit_progress(
                    self.store,
                    record,
                    "tool_completed",
                    {
                        "name": name,
                        "call_id": call_id,
                        "status": "SUCCEEDED",
                        "duration_ms": int((perf_counter() - started) * 1000),
                        "reused_from": cached[0] if cached else None,
                        "changes_count": len(result.get("changes", [])),
                        "activity": tool_activity(name, args, result),
                    },
                )
                if name == "submit_candidate":
                    return {"route": "verify_candidate"}
            except (ConfigurationError, ValueError, KeyError, TypeError) as exc:
                result = {
                    "status": "FAILED",
                    "code": exc.code
                    if isinstance(exc, ConfigurationError)
                    else "INVALID_CONFIGURATION",
                    "message": exc.message
                    if isinstance(exc, ConfigurationError)
                    else "工具输入或配置格式无效，请核对契约",
                }
                if name in {"analyze_materials", "inspect_material_region"}:
                    result["sources"] = record.tool_results.get(
                        "observation_attempt_" + call_id, {}
                    ).get("sources", [])
                log_failure(
                    logger,
                    exc,
                    task_id=sid,
                    turn_id=record.turn_id,
                    tool=name
                    if name in {t["function"]["name"] for t in configuration_tools()}
                    else "UNKNOWN_TOOL",
                    call_id=call_id,
                )
                failure_id = fingerprint(
                    [
                        name,
                        call["function"]["arguments"],
                        record.revision,
                        result["code"],
                        result["message"],
                    ]
                )
                record.failure_counts[failure_id] = record.failure_counts.get(failure_id, 0) + 1
                record.tool_results[call_id] = result
                tool_message(record, call_id, result)
                await emit_progress(
                    self.store,
                    record,
                    "tool_completed",
                    {
                        "name": name,
                        "call_id": call_id,
                        "status": "FAILED",
                        "code": result["code"],
                        "duration_ms": int((perf_counter() - started) * 1000),
                        "activity": tool_activity(name, args, result),
                    },
                )
                if isinstance(exc, ConfigurationError) and exc.code == "EXECUTION_STALLED":
                    raise
                if record.failure_counts[failure_id] >= 3:
                    raise ConfigurationError(
                        "EXECUTION_STALLED",
                        f"同一操作重复失败三次（{result['code']}），本轮已停止；已有依据和草稿保留，请核对失败原因后继续",
                    ) from exc
        return {"route": "reason"}

    async def _execute(self, r, name, args, call_id):
        if name == "reference_page_items":
            return add_references(r, args["fields"], args["samples"])
        if name == "read_draft":
            keys = args["field_keys"]
            if keys:
                return {
                    "revision": r.revision,
                    "page_id": r.draft["page_id"],
                    "version": r.draft["version"],
                    "fields": [f for f in r.draft.get("fields", []) if f["key"] in keys],
                }
            return {
                "revision": r.revision,
                "draft": {k: v for k, v in r.draft.items() if k != "fields"},
                "fields": [
                    {"key": f["key"], "label": f["label"]} for f in r.draft.get("fields", [])
                ],
            }
        if name == "read_tool_result":
            if args["tool_result_id"] not in r.tool_results:
                r.tool_results[args["tool_result_id"]] = await self.store.tool_result(
                    r.task_id, args["tool_result_id"]
                )
            selected = r.tool_results[args["tool_result_id"]]
            try:
                for token in args["pointer"].split("/")[1:]:
                    token = token.replace("~1", "/").replace("~0", "~")
                    if isinstance(selected, list):
                        if not token.isdecimal() or str(int(token)) != token:
                            raise KeyError(token)
                        selected = selected[int(token)]
                    elif isinstance(selected, dict):
                        selected = selected[token]
                    else:
                        raise KeyError(token)
            except (KeyError, IndexError) as exc:
                raise ConfigurationError(
                    "RESULT_PATH_NOT_FOUND", "工具结果中不存在该 JSON Pointer，请核对目录", 422
                ) from exc
            value = json.dumps(selected, ensure_ascii=False)
            reference = {"tool_result_id": args["tool_result_id"], "pointer": args["pointer"]}
            if args["offset"] == 0 and len(value) <= args["limit"]:
                return {**reference, "value": selected, "next_offset": None}
            end = args["offset"] + args["limit"]
            return {
                **reference,
                "content": value[args["offset"] : end],
                "next_offset": end if end < len(value) else None,
            }
        if name == "manage_requirements":
            return manage_requirements(r, args)
        if name == "assess_route":
            required = {"get_configuration_capabilities", "get_configuration"}
            if (
                not required.issubset(r.investigated_tools)
                or set(args["evidence_refs"]) - r.tool_results.keys()
            ):
                raise ConfigurationError(
                    "INVESTIGATION_REQUIRED",
                    "先读取当前配置与部署能力；evidence_refs 仅引用实际工具结果 ID，不包含需求 ID",
                )
            capabilities = await self.kernel.capabilities()
            evidence = [r.tool_results[k] for k in args["evidence_refs"]]
            if not any(
                isinstance(e, dict) and e.get("kernel_version") == capabilities["kernel_version"]
                for e in evidence
            ) or not any(
                isinstance(e, dict) and e.get("reference", {}).get("page_id") == r.draft["page_id"]
                for e in evidence
            ):
                raise ConfigurationError(
                    "INVESTIGATION_REQUIRED", "路线需引用目标页面配置和当前能力结果"
                )
            requirements = active_requirements(r)
            selected = set(args["requirement_ids"]) or {q["requirement_id"] for q in requirements}
            if selected - {q["requirement_id"] for q in requirements}:
                raise ConfigurationError("INVALID_REQUIREMENT", "路线只能关联当前有效目标")
            if args["route"] == "CONFIG":
                if not args.get("operations"):
                    raise ConfigurationError(
                        "ROUTE_PLAN_REQUIRED",
                        "可在线修改的判断必须提供具体 operations；口径不明确时先询问用户",
                        422,
                    )
                # The same kernel and maintenance boundary used by actual edits is authoritative.
                # Preflight returns a candidate without assigning it to the task or publishing it.
                self.kernel.edit_fields(r.draft, args["operations"])
            assessment = {
                **args,
                "intent_revision": r.intent_revision,
                "revision": r.revision,
                "baseline_digest": r.baseline_digest,
                "capability_digest": fingerprint(capabilities),
            }
            for requirement in requirements:
                if requirement["requirement_id"] in selected:
                    requirement["route_assessment"] = assessment
            routes = {
                (q.get("route_assessment") or {}).get("route", "UNDECIDED") for q in requirements
            }
            r.route = (
                "LOCAL_CODE"
                if "LOCAL_CODE" in routes
                else "UNDECIDED"
                if "UNDECIDED" in routes
                else args["route"]
            )
            r.route_assessment = {**assessment, "route": r.route}
            r.waiting_reason = "LOCAL_EXECUTION" if r.route == "LOCAL_CODE" else None
            return {
                **r.route_assessment,
                "requirements": [
                    {"requirement_id": q["requirement_id"], "assessment": q.get("route_assessment")}
                    for q in requirements
                ],
            }
        if name == "get_page_snapshot":
            if r.snapshot is None:
                r.waiting_reason = "CAPTURE"
                return {"status": "NEEDS_CAPTURE", "message": "请先关联已保存的页面采集再继续"}
            snapshot = r.snapshot.model_dump(mode="json")
            for field in snapshot["fields"]:
                if field["candidate_id"] not in r.reference_fields:
                    field["value"] = None
            # Discovery exposes candidate identities for targeted investigation.
            # Image URLs are only included after an explicit reference/analyze call.
            snapshot["samples"] = [
                {k: v for k, v in sample.items() if k != "url"} for sample in snapshot["samples"]
            ]
            return {**snapshot, "capture_transition": r.capture_transition}
        if name == "find_templates":
            return await self.kernel.context()
        if name == "get_configuration":
            context = await self.kernel.context(args["page_id"])
            if not args["field_keys"] and not args["material_types"]:
                return context
            source = context["editable"]
            keys = set(args["field_keys"])
            fields = [field for field in source.get("fields", []) if field["key"] in keys]
            materials = set(args["material_types"]) | {
                evidence["material"] for field in fields for evidence in field.get("evidence", [])
            }
            selected = [
                item for item in source.get("materials", []) if item["document_type"] in materials
            ]
            if keys - {field["key"] for field in fields} or materials - {
                item["document_type"] for item in selected
            }:
                raise ConfigurationError(
                    "CONFIGURATION_TARGET_NOT_FOUND",
                    "指定字段或材料不在当前活动配置中，请核对目录",
                    422,
                )
            return {
                "reference": context["reference"],
                "editable": {"fields": fields, "materials": selected},
                "maintenance": {
                    **context["maintenance"],
                    "fields": {
                        key: value
                        for key, value in context["maintenance"].get("fields", {}).items()
                        if key in keys
                    },
                },
                "dependencies": {key: context["dependencies"][key] for key in keys},
            }
        if name == "get_configuration_capabilities":
            return await self.kernel.capabilities()
        if name in {"analyze_materials", "inspect_material_region"}:
            if r.snapshot is None:
                raise ConfigurationError("CAPTURE_REQUIRED", "材料分析需要关联页面现场")
            if r.model_calls >= self.max_calls:
                raise ConfigurationError("BUDGET_EXCEEDED", "材料分析预算已耗尽")
            ids = args["sample_ids"] if name == "analyze_materials" else [args["sample_id"]]
            add_references(r, [], ids)
            samples = [s.model_dump() for s in r.snapshot.samples if s.sample_id in ids]
            if any(sample["source_kind"] != "http" for sample in samples):
                raise ConfigurationError(
                    "UNSUPPORTED_IMAGE_SOURCE",
                    "相关图片没有可供模型访问的 HTTP(S) 来源，请重新采集或说明材料",
                    422,
                )
            r.model_calls += 1
            sources = [
                {
                    "capture_id": r.capture_id,
                    "snapshot_id": r.snapshot.snapshot_id,
                    "sample_id": sample["sample_id"],
                    "url_digest": fingerprint(sample["url"]),
                    "observed_at": datetime.now(UTC).isoformat(),
                    "evidence_revision": r.evidence_revision,
                }
                for sample in samples
            ]
            # Preserve the attempted sources even when the provider cannot fetch them.
            r.tool_results["observation_attempt_" + call_id] = {
                "sources": sources,
                "status": "REQUESTED",
            }
            started = perf_counter()
            await emit_progress(
                self.store,
                r,
                "model_started",
                {
                    "model_call_id": call_id,
                    "parent_call_id": call_id,
                    "model_calls": r.model_calls,
                    "activity": {
                        "title": "等待材料模型返回",
                        "detail": "正在核对所引用的图片样本。",
                    },
                },
            )
            try:
                result = await self.model.analyze(
                    samples, args.get("focus", args.get("region", ""))
                )
            except Exception as exc:
                await emit_progress(
                    self.store,
                    r,
                    "model_failed",
                    {
                        "model_call_id": call_id,
                        "parent_call_id": call_id,
                        "status": "FAILED",
                        "code": exc.code
                        if isinstance(exc, ConfigurationError)
                        else "MODEL_REQUEST_FAILED",
                        "duration_ms": int((perf_counter() - started) * 1000),
                    },
                )
                raise
            for key, value in result.get("usage", {}).items():
                r.usage[key] = r.usage.get(key, 0) + value
            await emit_progress(
                self.store,
                r,
                "model_completed",
                {
                    "model_call_id": call_id,
                    "parent_call_id": call_id,
                    "duration_ms": int((perf_counter() - started) * 1000),
                    "usage": result.get("usage", {}),
                },
            )
            return {**result, "sources": sources}
        if name == "edit_draft":
            if args["expected_revision"] != r.revision:
                raise ConfigurationError("STALE_REVISION", "草稿版本已变化")
            if not isinstance(args["operations"], list) or not 1 <= len(args["operations"]) <= 100:
                raise ConfigurationError("INVALID_ARGUMENTS", "每次草稿操作数量应为 1—100", 422)
            before = r.draft
            await self.edit(r, [DraftOperation.model_validate(op) for op in args["operations"]])
            return {
                "revision": r.revision,
                "page_id": r.draft["page_id"],
                "version": r.draft["version"],
                "status": "DRAFT_UPDATED",
                "changes": changed_values(before, r.draft),
                "next_action": "核对实际变更，记录需求决策；不必重新读取整个草稿",
            }
        if name == "record_decision":
            if args.get("candidate_id") and (
                r.snapshot is None
                or args["candidate_id"] not in {f.candidate_id for f in r.snapshot.fields}
            ):
                raise ConfigurationError("INVALID_MAPPING", "字段引用必须属于当前采集")
            if args.get("sample_id") and (
                r.snapshot is None
                or args["sample_id"] not in {s.sample_id for s in r.snapshot.samples}
            ):
                raise ConfigurationError("INVALID_MAPPING", "图片引用必须属于当前采集")
            if args.get("field_key") and args["field_key"] not in {
                f["key"] for f in r.draft.get("fields", [])
            }:
                raise ConfigurationError("INVALID_MAPPING", "字段映射必须指向当前候选的稳定键")
            if args.get("material") and args["material"] not in {
                m["document_type"] for m in r.draft.get("materials", [])
            }:
                raise ConfigurationError("INVALID_MAPPING", "材料映射必须指向当前候选的材料类型")
            if not args.get("evidence_refs"):
                raise ConfigurationError("MISSING_EVIDENCE", "规则决策必须带依据引用", 422)
            if args["status"] not in {"RESOLVED", "NEEDS_INPUT", "UNSUPPORTED"}:
                raise ConfigurationError("INVALID_ARGUMENTS", "决策状态无效", 422)
            refs = args["evidence_refs"]
            if any(
                isinstance(r.tool_results.get(ref), dict)
                and any(
                    source.get("capture_id") != r.capture_id
                    or source.get("evidence_revision") != r.evidence_revision
                    for source in r.tool_results[ref].get("sources", [])
                )
                for ref in refs
            ):
                raise ConfigurationError(
                    "STALE_OBSERVATION", "引用的材料观察属于旧现场，请按当前来源重新分析"
                )
            valid_refs = {
                *([r.snapshot.snapshot_id] if r.snapshot else []),
                *r.reference_fields,
                *r.reference_samples,
                *(q["requirement_id"] for q in r.requirements),
                *r.tool_results.keys(),
            }
            if not isinstance(refs, list) or any(ref not in valid_refs for ref in refs):
                raise ConfigurationError(
                    "MISSING_EVIDENCE",
                    "依据必须引用真实 ID，可用引用："
                    + json.dumps(sorted(valid_refs)[:30], ensure_ascii=False),
                    422,
                )
            target = next(
                (
                    q
                    for q in active_requirements(r)
                    if q["requirement_id"] == args.get("requirement_id")
                ),
                None,
            )
            if args.get("requirement_id") and target is None:
                raise ConfigurationError("INVALID_REQUIREMENT", "只能决定当前有效目标")
            decision = {
                **args,
                "decision_id": call_id,
                "revision": r.revision,
                "intent_revision": r.intent_revision,
                "evidence_revision": r.evidence_revision,
                "requirement_revision": target.get("requirement_revision", 0) if target else None,
            }
            identities = [key for key in ("candidate_id", "sample_id") if args.get(key)] or [
                "requirement_id"
            ]
            for previous in r.decisions:
                if any(key in args and previous.get(key) == args[key] for key in identities):
                    previous["status"] = "SUPERSEDED"
            r.decisions.append(decision)
            if args.get("requirement_id"):
                r.capability_gaps = [
                    gap for gap in r.capability_gaps if gap.requirement_id != args["requirement_id"]
                ]
            if args.get("requirement_id") and args["status"] == "RESOLVED":
                for requirement in r.requirements:
                    if (
                        requirement["requirement_id"] == args["requirement_id"]
                        and requirement.get("status") == "PENDING"
                    ):
                        requirement["status"] = "ACTIVE"
            if args["status"] == "UNSUPPORTED":
                r.capability_gaps.append(
                    CapabilityGap(
                        requirement_id=args.get("requirement_id"),
                        missing_capability=args["reason"],
                        evidence_refs=refs,
                    )
                )
                r.waiting_reason = (
                    "LOCAL_EXECUTION" if r.route == "LOCAL_CODE" else "CAPABILITY_INVESTIGATION"
                )
            return decision
        if name in {"inspect_candidate", "validate_candidate"}:
            preview = await self.kernel.validate(r)
            source = (await self.kernel.context(r.draft["page_id"]))["source"]
            if r.snapshot is None:
                r.waiting_reason = "CAPTURE"
            return {
                **{
                    k: preview[k]
                    for k in ("page_id", "candidate_digest", "effective_digest", "kernel_version")
                },
                "revision": r.revision,
                "status": "COMPILED",
                "changes": changed_values(source, r.draft),
                "publishable": False,
                "verification_scope": "配置编译与依赖；完整发布验证由 submit_candidate 执行",
                "waiting_reason": r.waiting_reason,
                "next_action": "候选已保留；等待用户关联现场后继续试采集和独立回放"
                if r.snapshot is None
                else "完成当前候选试采集与独立回放后提交完整验证",
            }
        if name in {"ask_user", "request_collection_trial"}:
            if name == "request_collection_trial" and r.snapshot is None:
                r.waiting_reason = "CAPTURE"
                raise ConfigurationError("CAPTURE_REQUIRED", "需要从扩展采集并关联页面后试采集")
            pending = {
                "intent_revision": r.intent_revision,
                "id": call_id,
                "tool_call_id": call_id,
                "revision": r.revision,
                "kind": "question" if name == "ask_user" else "browser",
            }
            if name == "ask_user":
                pending.update(question=args["question"], options=args.get("options", []))
                r.state = "NEEDS_INPUT"
            else:
                preview = await self.kernel.validate(r)
                pending.update(
                    binding=trial_binding(r),
                    manifest=preview["manifest"],
                    candidate_digest=preview["candidate_digest"],
                    page_catalog=preview["page_catalog"],
                    page_id=preview["page_id"],
                    snapshot_id=r.snapshot.snapshot_id,
                    structure_digest=r.snapshot.structure_digest,
                    page_instance_id=r.snapshot.page_instance_id,
                    page_url=r.snapshot.page_url,
                )
                pending["expires_at"] = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
            r.pending = pending
            return {}
        if name == "run_replay":
            result = await self.kernel.replay(r, args["case_set_id"])
            return result
        if name == "submit_candidate":
            return {"status": "SUBMITTED"}
        raise ConfigurationError("UNKNOWN_TOOL", "未注册的工具", 422)
