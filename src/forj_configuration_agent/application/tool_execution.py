"""Strict model tool execution; business operations stay in the configuration backend."""

import json
import logging
from datetime import UTC, datetime, timedelta

from forj_configuration_agent.application.conversation import fingerprint, tool_message
from forj_configuration_agent.application.diagnostics import log_failure
from forj_configuration_agent.application.validation import trial_binding
from forj_configuration_agent.contracts.models import AgentError, CapabilityGap, DraftOperation
from forj_configuration_agent.tools.arguments import validate_arguments
from forj_configuration_agent.tools.catalog import configuration_tools

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, store, kernel, model, max_calls, edit):
        self.store, self.kernel, self.model = store, kernel, model
        self.max_calls, self.edit = max_calls, edit

    def available_tools(self, record):
        return [tool for tool in configuration_tools() if record.selected_samples or
                tool["function"]["name"] not in {"analyze_materials", "inspect_material_region"}]

    async def execute_tools(self, state):
        sid = state["session_id"]
        record = self.store.get(sid)
        calls = next(m["tool_calls"] for m in reversed(record.messages) if m.get("tool_calls"))
        for call in calls:
            record = self.store.get(sid)
            call_id, name = call["id"], call["function"]["name"]
            if call_id in record.tool_results:
                tool_message(record, call_id, record.tool_results[call_id])
                self.store.put(record)
                continue
            if record.tool_calls >= 120:
                raise AgentError("BUDGET_EXCEEDED", "本轮工具预算耗尽，草稿已保留")
            record.tool_calls += 1
            self.store.put(record, "tool_started", {"name": name, "call_id": call_id,
                "turn_id": record.turn_id, "revision": record.revision})
            try:
                raw_args = json.loads(call["function"]["arguments"])
                if name not in {t["function"]["name"] for t in self.available_tools(record)}:
                    raise AgentError("UNKNOWN_TOOL", "未注册或当前范围不可用的工具", 422)
                args = validate_arguments(name, raw_args)
                if name in {"ask_user", "request_collection_trial", "submit_candidate"} and len(calls) != 1:
                    raise AgentError("INVALID_TOOL_BATCH", "暂停或提交工具必须单独调用", 422)
                result = await self._execute(record, name, args, call_id)
                if record.pending:
                    self.store.put(record, "question_pending" if name == "ask_user" else "browser_task_pending")
                    return {"route": "await_user" if name == "ask_user" else "await_browser"}
                record.tool_results[call_id] = result
                tool_message(record, call_id, result)
                self.store.put(record, "tool_completed", {"name": name, "call_id": call_id, "status": "SUCCEEDED"})
                if name == "submit_candidate":
                    return {"route": "verify_candidate"}
            except (AgentError, ValueError, KeyError, TypeError) as exc:
                result = {"status": "FAILED", "code": exc.code if isinstance(exc, AgentError) else "INVALID_CONFIGURATION",
                          "message": exc.message if isinstance(exc, AgentError) else "工具输入或配置格式无效，请核对契约"}
                log_failure(logger, exc, session_id=sid, turn_id=record.turn_id,
                            tool=name if name in {t["function"]["name"] for t in configuration_tools()} else "UNKNOWN_TOOL",
                            call_id=call_id)
                failure_id = fingerprint([name, call["function"]["arguments"], record.revision, result["code"], result["message"]])
                record.failure_counts[failure_id] = record.failure_counts.get(failure_id, 0) + 1
                record.tool_results[call_id] = result
                tool_message(record, call_id, result)
                self.store.put(record, "tool_completed", {"name": name, "call_id": call_id, "status": "FAILED", "code": result["code"]})
                if record.failure_counts[failure_id] >= 3:
                    raise AgentError("BUDGET_EXCEEDED", "同一操作连续确定性失败三次，草稿已保留；请补充信息后恢复") from exc
        return {"route": "reason"}

    async def _execute(self, r, name, args, call_id):
        if name == "get_page_snapshot":
            snapshot = r.snapshot.model_dump(mode="json")
            for field in snapshot["fields"]:
                if field["candidate_id"] not in r.selected_fields:
                    field["value"] = None
            snapshot["samples"] = [sample for sample in snapshot["samples"] if sample["sample_id"] in r.selected_samples]
            return snapshot
        if name == "find_templates":
            return self.kernel.context()
        if name == "get_configuration":
            return self.kernel.context(args["page_id"])
        if name == "get_configuration_capabilities":
            return self.kernel.capabilities()
        if name in {"analyze_materials", "inspect_material_region"}:
            if r.model_calls >= self.max_calls:
                raise AgentError("BUDGET_EXCEEDED", "材料分析预算已耗尽")
            ids = args["sample_ids"] if name == "analyze_materials" else [args["sample_id"]]
            if set(ids) - set(r.selected_samples):
                raise AgentError("INVALID_SAMPLE", "只能分析本会话已选图片", 422)
            samples = [s.model_dump() for s in r.snapshot.samples if s.sample_id in ids]
            r.model_calls += 1
            self.store.put(r, "progress", {"stage": "正在核对材料", "model_calls": r.model_calls})
            result = await self.model.analyze(samples, args.get("focus", args.get("region", "")))
            for key, value in result.get("usage", {}).items():
                r.usage[key] = r.usage.get(key, 0) + value
            return result
        if name == "edit_draft":
            if args["expected_revision"] != r.revision:
                raise AgentError("STALE_REVISION", "草稿版本已变化")
            if not isinstance(args["operations"], list) or not 1 <= len(args["operations"]) <= 100:
                raise AgentError("INVALID_ARGUMENTS", "每次草稿操作数量应为 1—100", 422)
            self.edit(r, [DraftOperation.model_validate(op) for op in args["operations"]])
            return {"revision": r.revision, "draft": r.draft}
        if name == "record_decision":
            if not args.get("evidence_refs"):
                raise AgentError("MISSING_EVIDENCE", "规则决策必须带依据引用", 422)
            if args["status"] not in {"RESOLVED", "NEEDS_INPUT", "UNSUPPORTED"}:
                raise AgentError("INVALID_ARGUMENTS", "决策状态无效", 422)
            refs = args["evidence_refs"]
            valid_refs = {r.snapshot.snapshot_id, *r.selected_fields, *r.selected_samples,
                          *(q["requirement_id"] for q in r.requirements), *r.tool_results.keys()}
            if not isinstance(refs, list) or any(ref not in valid_refs for ref in refs):
                raise AgentError("MISSING_EVIDENCE", "依据必须引用真实 ID，可用引用：" + json.dumps(sorted(valid_refs)[:30], ensure_ascii=False), 422)
            decision = {**args, "decision_id": call_id, "revision": r.revision}
            identities = [key for key in ("candidate_id", "sample_id") if key in args] or ["requirement_id"]
            for previous in r.decisions:
                if any(key in args and previous.get(key) == args[key] for key in identities):
                    previous["status"] = "SUPERSEDED"
            r.decisions.append(decision)
            if args["status"] == "UNSUPPORTED":
                r.capability_gaps.append(CapabilityGap(requirement_id=args.get("requirement_id"),
                    missing_capability=args["reason"], evidence_refs=refs))
                r.state = "BLOCKED"
                r.error = "当前能力无法表达此要求：" + args["reason"]
            return decision
        if name in {"inspect_candidate", "validate_candidate"}:
            return self.kernel.validate(r)
        if name in {"ask_user", "request_collection_trial"}:
            pending = {"id": call_id, "tool_call_id": call_id, "revision": r.revision,
                       "kind": "question" if name == "ask_user" else "browser"}
            if name == "ask_user":
                pending.update(question=args["question"], options=args.get("options", []))
                r.state = "NEEDS_INPUT"
            else:
                preview = self.kernel.validate(r)
                pending.update(binding=trial_binding(r), manifest=preview["manifest"], candidate_digest=preview["candidate_digest"],
                               page_catalog=preview["page_catalog"], page_id=preview["page_id"],
                               snapshot_id=r.snapshot.snapshot_id, structure_digest=r.snapshot.structure_digest,
                               page_instance_id=r.snapshot.page_instance_id, page_url=r.snapshot.page_url)
                pending["expires_at"] = (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
            r.pending = pending
            return {}
        if name == "run_replay":
            result = await self.kernel.replay(r, args["case_set_id"])
            return result
        if name == "submit_candidate":
            return {"status": "SUBMITTED"}
        raise AgentError("UNKNOWN_TOOL", "未注册的工具", 422)

